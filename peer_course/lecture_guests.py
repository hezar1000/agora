"""Guests in a real lecture: a code and a name, gone when the lecture ends.

A course that allows it (``Course.lecture_guests``) shows its instructor a
six-digit code for the open lecture. Anyone who types it on the join page with
a name becomes a ``guest`` member: they raise hands, are called on and answer
polls like a student, but every grade, export, attendance count and roster
asks for ``role="student"``, so none of them ever sees a guest.

When the lecture ends the guests go with everything they did -- hands, calls,
poll answers, messages -- by cascade. A guest who came in without an account
is deleted outright and is confined to this one course until then, like a
demonstration-class guest (``sandbox_guard``). Someone who typed the code while
signed in to a real account keeps the account; only the guest membership goes.
"""

import random
import string
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .models import Course, CourseMember

CODE_LENGTH = 6

#: A lecture hall, not a stadium; also bounds what one code can create.
MAX_GUESTS_PER_LECTURE = 300

#: Backstop for a lecture left open far past its time: guest accounts older
#: than this go whether or not their lecture has ended.
MAX_AGE = timedelta(hours=24)


def _codes_in_use():
    from peer_lecture.models import Lecture

    lectures = Lecture.objects.filter(end_time__isnull=True).exclude(guest_code="")
    sandboxes = Course.objects.filter(is_sandbox=True).exclude(join_code="")
    return set(lectures.values_list("guest_code", flat=True)) | set(
        sandboxes.values_list("join_code", flat=True)
    )


def code_for(lecture):
    """The open lecture's guest code, made on first use."""
    from peer_lecture.models import Lecture

    if lecture.guest_code:
        return lecture.guest_code
    with transaction.atomic():
        locked = Lecture.objects.select_for_update().get(pk=lecture.pk)
        if not locked.guest_code:
            used = _codes_in_use()
            for _ in range(200):
                code = "".join(random.choices(string.digits, k=CODE_LENGTH))
                if code not in used:
                    break
            else:
                raise RuntimeError("could not find an unused guest code")
            # update(), not save(): Lecture.save ends-of-lecture hooks are for
            # closing a lecture, and this one is open.
            Lecture.objects.filter(pk=locked.pk).update(guest_code=code)
            locked.guest_code = code
    lecture.guest_code = locked.guest_code
    return lecture.guest_code


def lecture_for_code(code):
    """The open lecture a guest code belongs to, if its course still allows guests."""
    from peer_lecture.models import Lecture

    digits = "".join(character for character in str(code or "") if character.isdigit())
    if len(digits) != CODE_LENGTH:
        return None
    return Lecture.objects.select_related("course").filter(
        guest_code=digits,
        end_time__isnull=True,
        course__lecture_guests=True,
        course__archived=False,
        course__is_sandbox=False,
    ).first()


def guest_count(course_id):
    return CourseMember.objects.filter(course_id=course_id, role="guest").count()


def join(lecture, *, user=None, name=""):
    """Add a guest to the lecture's course. Returns (member, created_user) or None if full.

    ``user`` is a signed-in real account; without one a temporary account is
    made from ``name``.
    """
    course = lecture.course
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=course.pk)
        if user is not None:
            existing = CourseMember.objects.filter(course=course, user=user).first()
            if existing is not None:
                return existing, None
        if guest_count(course.pk) >= MAX_GUESTS_PER_LECTURE:
            return None
        created = None
        if user is None:
            from .sandbox import _unique_username

            first, _, last = name.partition(" ")
            created = User.objects.create_user(
                username=_unique_username(f"guest-lecture-{lecture.pk}"),
                first_name=first[:30],
                last_name=last[:30],
            )
            created.set_unusable_password()
            created.save(update_fields=["password"])
        member = CourseMember.objects.create(
            course=course,
            user=user or created,
            role="guest",
            active=True,
            # Confines an account made here to this course (sandbox_guard);
            # a real account is not confined and keeps everything else.
            is_sandbox_guest=created is not None,
        )
    return member, created


def remove_guests(course_id):
    """Delete every guest of a course: their accounts, or just the membership."""
    removed = 0
    with transaction.atomic():
        for member in CourseMember.objects.filter(
            course_id=course_id, role="guest"
        ).select_related("user"):
            user = member.user
            member.delete()
            # Only an account this feature made, and only once it is a member
            # of nothing at all.
            if (
                not user.has_usable_password()
                and user.username.startswith("guest-lecture-")
                and not user.memberships.exists()
            ):
                user.delete()
            removed += 1
    return removed


def sweep(now=None):
    """Remove guests whose lecture is over, and any guest account past MAX_AGE.

    Ending a lecture removes its guests already (Lecture.save); this catches a
    lecture closed some other way, and one left open for more than a day.
    """
    from peer_lecture.models import Lecture

    now = now or timezone.now()
    courses = set(
        CourseMember.objects.filter(role="guest").values_list("course_id", flat=True)
    )
    open_courses = set(
        Lecture.objects.filter(course_id__in=courses, end_time__isnull=True)
        .values_list("course_id", flat=True)
    )
    removed = 0
    for course_id in courses - open_courses:
        removed += remove_guests(course_id)
    stale = User.objects.filter(
        username__startswith="guest-lecture-",
        date_joined__lt=now - MAX_AGE,
        memberships__role="guest",
    ).distinct()
    for user in stale:
        if not user.has_usable_password():
            user.delete()
            removed += 1
    return removed
