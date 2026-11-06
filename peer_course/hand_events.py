"""Record every hand raise as an event rather than a running total.

Why a model hook and not calls at the view sites: a hand field is flipped in
roughly forty places across ``peer_home.views``, duplicated once per list and
again per role, and ``clear_all`` iterates students one save at a time. Wiring
each of those by hand would miss one today and miss every branch added later.
``CourseMember.save`` is the one place they all pass through.

Bulk ``.update()`` bypasses signals, so it must be paired with an explicit
call. There is exactly one such write against a student: ``_select_student``
lowers the chosen hand with a conditional UPDATE used as a compare-and-swap,
and calls :func:`record_lowered` immediately after. Every other bulk update on
a hand field targets instructors, whose ``hand_up`` means "this queue is open"
rather than "a hand is raised" and so is deliberately not an event.
``test_a_bulk_update_on_a_student_hand_is_paired_with_a_record`` fails if a new
one appears unpaired.

Nothing here changes existing behaviour. The boolean fields and the point
totals remain exactly as they were and remain authoritative for grading; this
only writes an audit trail beside them.
"""

import contextlib
import contextvars
import logging

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from .models import CourseMember, HandRaise, LEARNER_ROLES
from .sessions import active_session_for_course

logger = logging.getLogger(__name__)

#: Which boolean on ``CourseMember`` backs which participation list. The list
#: identity lives in the column name, which is the reason the view layer is
#: duplicated four ways; recording maps it back to data.
HAND_FIELDS = {
    1: "hand_up",
    2: "hand_up_list_2",
    3: "hand_up_list_3",
    4: "hand_up_list_4",
}

_SNAPSHOT = "_agora_hand_snapshot"

# A hand going down looks identical from the model layer whoever lowered it, so
# the caller declares the reason. A ContextVar rather than a module global
# because Agora runs on Daphne and concurrent lectures share a process.
_resolution = contextvars.ContextVar("agora_hand_resolution")


@contextlib.contextmanager
def resolving_as(resolution):
    """Declare why hands lowered in this block came down."""
    token = _resolution.set(resolution)
    try:
        yield
    finally:
        _resolution.reset(token)


def _current_resolution():
    return _resolution.get(HandRaise.Resolution.LOWERED)


def _read_hands(instance):
    """Current hand state, skipping fields this instance did not load."""
    deferred = instance.get_deferred_fields()
    return {
        list_id: bool(getattr(instance, field))
        for list_id, field in HAND_FIELDS.items()
        if field not in deferred
    }


@receiver(post_init, sender=CourseMember)
def _snapshot_hands(sender, instance, **kwargs):
    # Taken on load so the save can diff without re-reading the row. Costs four
    # attribute reads, no query.
    setattr(instance, _SNAPSHOT, _read_hands(instance))


@receiver(post_save, sender=CourseMember)
def _record_hand_transitions(
    sender, instance, created=False, raw=False, update_fields=None, **kwargs
):
    # `raw` marks a fixture replay, which supplies its own rows; writing events
    # there would invent history that never happened.
    if raw:
        return
    if update_fields is not None and not (
        set(update_fields) & set(HAND_FIELDS.values())
    ):
        return

    current = _read_hands(instance)
    previous = getattr(instance, _SNAPSHOT, None)
    if previous is None:
        # Never loaded from the database, so there is no prior state to diff
        # against; treat a raised hand as newly raised and nothing else.
        previous = {list_id: False for list_id in current}

    changed = {
        list_id: up
        for list_id, up in current.items()
        if previous.get(list_id) != up
    }
    setattr(instance, _SNAPSHOT, current)
    if not changed:
        return

    # An instructor's `hand_up` means "this queue is open", not "a hand is
    # raised". Recording it would put the instructor in every lecture's
    # participation history.
    if instance.role != "student":
        return

    for list_id, is_up in changed.items():
        try:
            if is_up:
                _open(instance, list_id)
            else:
                _close(instance, list_id, _current_resolution())
        except Exception:
            # An audit trail must never be able to break a lecture in progress.
            logger.exception(
                "Could not record hand transition for member %s list %s",
                instance.pk,
                list_id,
            )


def _session_for(member):
    try:
        return active_session_for_course(member.course, manual_active=True)
    except Exception:
        logger.exception("Could not resolve session for member %s", member.pk)
        return None


def _open(member, list_id):
    if HandRaise.objects.filter(
        member=member, participation_list=list_id, resolved_at__isnull=True
    ).exists():
        # Already up. The boolean is idempotent, so this happens whenever a view
        # re-saves an unchanged member, and must not stack rows.
        return
    with transaction.atomic():
        HandRaise.objects.create(
            member=member,
            session=_session_for(member),
            participation_list=list_id,
        )


def _close(member, list_id, resolution):
    from django.utils import timezone

    return HandRaise.objects.filter(
        member=member, participation_list=list_id, resolved_at__isnull=True
    ).update(resolved_at=timezone.now(), resolution=resolution)


def record_lowered(member, list_id, resolution):
    """Record a hand coming down that the model hook cannot see.

    For the one write that lowers a student's hand with a queryset update
    rather than a save. Also syncs the in-memory snapshot, so a later save of
    the same instance does not read the transition a second time.
    """
    closed = _close(member, list_id, resolution)
    snapshot = getattr(member, _SNAPSHOT, None)
    if snapshot is not None and list_id in snapshot:
        snapshot[list_id] = False
    return closed


def clear_student_hands(course, resolution=HandRaise.Resolution.CLEARED):
    """Lower every student hand in ``course`` and close its event row.

    Lecture boundaries are hard boundaries for hand raises.  Clearing at both
    ends of that boundary prevents a browser left open after one lecture from
    silently appearing in the next lecture's queue, while the ordinary model
    hook preserves the audit trail for every hand that was cleared.

    A roll call still open is closed first, from the hands as they stand:
    every lecture ends through here, and ending one mid-roll-call must not
    throw the answers away.
    """
    from .attendance import close_open_check

    close_open_check(course)
    raised = CourseMember.objects.filter(
        course=course,
        role__in=LEARNER_ROLES,
    ).filter(
        Q(hand_up=True)
        | Q(hand_up_list_2=True)
        | Q(hand_up_list_3=True)
        | Q(hand_up_list_4=True)
    )
    cleared = 0
    with resolving_as(resolution):
        for member in raised:
            changed = []
            for field in HAND_FIELDS.values():
                if getattr(member, field):
                    setattr(member, field, False)
                    changed.append(field)
            if changed:
                member.save(update_fields=changed)
                cleared += 1
    return cleared
