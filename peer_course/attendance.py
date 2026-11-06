"""Attendance mode: a roll call that someone outside the room cannot answer.

Every list opens and the instructor names one out loud. The people in the room
raise their hand in that list. Someone following along from elsewhere cannot
hear it and has to guess, so a hand in any other list is put in front of the
instructor to be called on: either they answer, and were in the room after
all, or they do not, and are kept on a list to follow up.

Two things keep the guess a guess:

* The announced list is only ever sent in answer to an instructor's own
  request. Broadcasts say that a roll call opened or closed, never which list.
* While a roll call is open a student cannot read the other hands
  (``count_hands_up`` answers them as it does a student with no hand up), so
  nobody can see where the room went and follow it.

Nothing here touches participation credit. Calling on a student credits every
hand that is up, so selection is refused while a roll call is open, and the
hands are cleared when it closes.
"""

import random

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import hand_events
from .models import (
    AttendanceCheck,
    AttendanceRecord,
    Course,
    CourseMember,
    HandRaise,
)
from .sessions import active_session_for_course

Status = AttendanceRecord.Status

#: The instructor flag that holds each list open. See ``hand_events``: on an
#: instructor, ``hand_up`` means "this list is open".
LIST_FIELDS = hand_events.HAND_FIELDS


class AttendanceError(Exception):
    """Something the instructor needs to be told, in a sentence."""


def visible_lists(course):
    """The lists students can see a button for, in order."""
    config = course.get_color_config_dict()
    return [
        position
        for position, colour in Course.QUEUE_COLOURS.items()
        if config.get(f"hidden_{colour}") is not True
    ]


def open_check(course):
    return AttendanceCheck.objects.filter(
        course=course, closed_at__isnull=True
    ).first()


def is_open(course):
    return AttendanceCheck.objects.filter(
        course=course, closed_at__isnull=True
    ).exists()


def _lecture_session(course):
    # Imported here: peer_lecture imports from peer_course at load time.
    from peer_lecture.models import Lecture

    lecture = Lecture.currentLecture(course.pk)
    if lecture is None:
        return None
    return active_session_for_course(
        course, manual_active=bool(course.manual_override and lecture)
    )


def _instructors(course):
    # The same rows check_status reads to decide whether a list is open.
    return CourseMember.objects.filter(course=course, role="instructor")


def _open_lists(course):
    rows = list(_instructors(course).values_list(*LIST_FIELDS.values()))
    return {
        str(position): any(row[index] for row in rows)
        for index, position in enumerate(LIST_FIELDS)
    }


def _broadcast(course, key, user):
    # After commit, so a student's refetch cannot arrive before the change.
    def send():
        async_to_sync(get_channel_layer().group_send)(
            f"course_{course.pk}",
            {
                "type": "send_message",
                "key": key,
                # Both instructor devices need attendance updates, including
                # other tabs signed in as the person making the decision.
                "send_auth_id": "system",
            },
        )

    transaction.on_commit(send)


def start(course, user, announced_list=None):
    """Open a roll call in the running lecture, or return the one already open."""
    with transaction.atomic():
        course = Course.objects.select_for_update().get(pk=course.pk)
        existing = open_check(course)
        if existing is not None:
            return existing
        session = _lecture_session(course)
        if session is None:
            raise AttendanceError("Start the lecture before taking attendance.")
        lists = visible_lists(course)
        if len(lists) < 2:
            raise AttendanceError(
                "Attendance needs at least two lists students can see."
            )
        if announced_list is None:
            # SystemRandom: people are bad at picking at random, and an
            # instructor who "always says Red" is soon worked out.
            announced_list = random.SystemRandom().choice(lists)
        elif announced_list not in lists:
            raise AttendanceError("That list is not one students can see.")

        before = _open_lists(course)
        # Hands already up were raised for something else, and must not count
        # as an answer to a question not yet asked.
        hand_events.clear_student_hands(course)
        check = AttendanceCheck.objects.create(
            course=course,
            session=session,
            announced_list=announced_list,
            started_by=user if getattr(user, "pk", None) else None,
            lists_open_before=before,
        )
        _instructors(course).update(
            **{LIST_FIELDS[position]: True for position in lists}
        )
        _broadcast(course, "enable-hand", user)
        _broadcast(course, "attendance-open", user)
        return check


def announce(check, announced_list):
    """Change the list the instructor says they named, while it is open."""
    if not check.is_open:
        raise AttendanceError("This roll call is already closed.")
    if announced_list not in visible_lists(check.course):
        raise AttendanceError("That list is not one students can see.")
    check.announced_list = announced_list
    check.save(update_fields=["announced_list"])
    return check


def _classify(lists_raised, announced_list):
    if lists_raised == [announced_list]:
        return Status.PRESENT
    # Any other list, including the right one alongside others: in a course
    # that allows several lists at once, a hand in all four is the guess.
    return Status.UNREVIEWED


def close(check, user=None):
    """Record every hand, clear them, and put the lists back as they were."""
    with transaction.atomic():
        course = Course.objects.select_for_update().get(pk=check.course_id)
        check = AttendanceCheck.objects.select_for_update().get(pk=check.pk)
        if not check.is_open:
            return check
        raised = (
            CourseMember.objects.filter(course=course, role="student", active=True)
            .filter(
                Q(hand_up=True)
                | Q(hand_up_list_2=True)
                | Q(hand_up_list_3=True)
                | Q(hand_up_list_4=True)
            )
        )
        records = []
        for member in raised:
            lists_raised = [
                position
                for position, field in LIST_FIELDS.items()
                if getattr(member, field)
            ]
            records.append(
                AttendanceRecord(
                    roll_call=check,
                    member=member,
                    lists_raised=lists_raised,
                    status=_classify(lists_raised, check.announced_list),
                )
            )
        AttendanceRecord.objects.bulk_create(records)
        check.closed_at = timezone.now()
        check.save(update_fields=["closed_at"])
        # Closed first: clear_student_hands finalises any open roll call, and
        # this one is finished.
        hand_events.clear_student_hands(
            course, resolution=HandRaise.Resolution.ATTENDANCE
        )
        before = check.lists_open_before or {}
        _instructors(course).update(
            **{
                field: bool(before.get(str(position), False))
                for position, field in LIST_FIELDS.items()
            }
        )
        _broadcast(course, "disable-hand", user)
        _broadcast(course, "attendance-closed", user)
        return check


def close_open_check(course):
    """Finalise a roll call left open when the lecture ends."""
    check = open_check(course)
    if check is not None:
        close(check)
    return check


def review(record, status, user):
    """Settle a hand in the wrong list, or change how it was settled."""
    allowed = (Status.CONFIRMED, Status.NOT_IN_CLASS, Status.UNREVIEWED)
    if status not in allowed:
        raise AttendanceError("Unknown attendance decision.")
    if record.status in (Status.PRESENT, Status.ADDED):
        raise AttendanceError("This student was not in a wrong list.")
    record.status = status
    if status == Status.UNREVIEWED:
        record.reviewed_at = None
        record.reviewed_by = None
    else:
        record.reviewed_at = timezone.now()
        record.reviewed_by = user
    record.save(update_fields=["status", "reviewed_at", "reviewed_by"])
    _broadcast(record.roll_call.course, "attendance-reviewed", user)
    return record


def mark_present(check, member, user):
    """Add someone who had no hand up: late, a dead phone, a flat battery."""
    if check.is_open:
        raise AttendanceError("Close the roll call first.")
    if member.course_id != check.course_id or member.role != "student":
        raise AttendanceError("That person is not a student in this course.")
    record, created = AttendanceRecord.objects.get_or_create(
        roll_call=check,
        member=member,
        defaults={
            "status": Status.ADDED,
            "reviewed_at": timezone.now(),
            "reviewed_by": user,
        },
    )
    if not created:
        raise AttendanceError("That student already has a record here.")
    return record


def remove_added(record):
    """Undo ``mark_present``. Hands the room raised are never deleted."""
    if record.status != Status.ADDED:
        raise AttendanceError("Only a student added by hand can be removed.")
    record.delete()


def live_counts(check):
    """Hands per list right now, for the instructor's screen only."""
    students = CourseMember.objects.filter(
        course_id=check.course_id, role="student", active=True
    )
    counts = students.aggregate(
        **{
            str(position): Count("id", filter=Q(**{field: True}))
            for position, field in LIST_FIELDS.items()
        }
    )
    return {int(position): n for position, n in counts.items()}


def summary(check):
    """Counts by outcome, and who has no hand up at all."""
    by_status = dict(
        check.records.values_list("status").annotate(n=Count("id"))
    )
    enrolled = CourseMember.objects.filter(
        course_id=check.course_id, role="student", active=True
    ).count()
    recorded = sum(by_status.values())
    attended = sum(by_status.get(status, 0) for status in AttendanceRecord.ATTENDED)
    return {
        "enrolled": enrolled,
        "present": by_status.get(Status.PRESENT, 0),
        "confirmed": by_status.get(Status.CONFIRMED, 0),
        "added": by_status.get(Status.ADDED, 0),
        "not_in_class": by_status.get(Status.NOT_IN_CLASS, 0),
        "unreviewed": by_status.get(Status.UNREVIEWED, 0),
        "attended": attended,
        "no_hand": max(0, enrolled - recorded),
    }


def absent_students(check):
    """Enrolled students with no record: no hand up when it closed."""
    return (
        CourseMember.objects.filter(
            course_id=check.course_id, role="student", active=True
        )
        .exclude(attendance_records__roll_call=check)
        .select_related("user", "user__profile")
    )
