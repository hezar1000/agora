"""A demonstration class that works the moment it is made.

Agora already had a "one-time session". Two buttons on the login page made a
`temp_instructor_<uuid>` account and a course called "temp session", or a
`temp_student_<uuid>` account and dropped you on the enrolment page. Read the
code and you can see why nobody liked it:

* The course had no session and no lecture, so no queue could be open and no
  hand could be raised. The demo could not demonstrate anything.
* It had no students. Agora's whole point is choosing between raised hands,
  which needs more than one hand.
* The two buttons did not meet. A student had to be told the instructor's
  24-character enrolment code -- `VkQST4X0IgojO26-PM3QA3eQ` -- out loud.
* Cleanup happened only if someone pressed Log out. Closing the tab left the
  account and the course behind for good.

So this builds the opposite: one action produces a class with a lecture already
running, a six-digit code anyone in the room can type, and an expiry that
removes all of it whether or not anybody logs out. The people trying it out are
the students; there are no invented ones.

Both doors are on the sign-in page, so neither needs an account: starting one
makes a temporary instructor, and joining one makes a temporary student. Both
are guests of that one class (`sandbox_guard`) and are deleted with it. Someone
already signed in with a real account keeps it: they own or join the class as
themselves, and only their membership in it is deleted.
"""

import random
import string
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from peer_lecture.models import Lecture

from . import participation_presets as presets
from .models import Course, CourseMember, CourseSession, HandRaise

#: Long enough to run a meeting, short enough that a forgotten one goes away.
LIFETIME = timedelta(hours=3)

#: Read aloud to a room, so digits rather than a random 24-character string.
CODE_LENGTH = 6

NAME_PREFIX = "Demo class"

#: Anyone on the internet can start one from the sign-in page, so cap how many
#: classes made by temporary instructors may be live at once.
MAX_LIVE_GUEST_CLASSES = 100

def _fresh_code():
    """A six-digit code no live sandbox is already using."""
    # One join page takes both kinds of code, so avoid live lectures' too.
    from .lecture_guests import _codes_in_use

    live = _codes_in_use()
    for _ in range(200):
        code = "".join(random.choices(string.digits, k=CODE_LENGTH))
        if code not in live:
            return code
    raise RuntimeError("could not find an unused join code")


def _unique_username(base):
    for suffix in range(10_000):
        candidate = base if suffix == 0 else f"{base}-{suffix}"
        if not User.objects.filter(username=candidate).exists():
            return candidate
    raise RuntimeError(f"could not find a free username near {base!r}")


def guest_classes_full(now=None):
    """Whether starting another class without an account must be refused."""
    now = now or timezone.now()
    return Course.objects.filter(
        is_sandbox=True, sandbox_expires_at__gt=now,
        members__role="instructor", members__is_sandbox_guest=True,
    ).distinct().count() >= MAX_LIVE_GUEST_CLASSES


@transaction.atomic
def create(owner, *, guest_owner=False, now=None):
    """A demonstration class, ready to teach, owned by ``owner``.

    Empty of students on purpose. The people trying it out are the students:
    they open the join page in another browser, type the code and a name. A
    crowd of invented classmates raising their hands on a timer looks busy and
    teaches nobody anything about their own course.

    What the old one-time session lacked and this has: a lecture that is open
    right now, queues that are open, and a code short enough to say out loud.
    """
    now = now or timezone.now()
    code = _fresh_code()
    course = Course.objects.create(
        # Named after its own code, so two sandboxes in one meeting are told
        # apart at a glance -- the old one called them all "temp session".
        displayname=f"{NAME_PREFIX} {code}",
        browsable=False,
        archived=False,
        on_list=False,
        agora_mode=True,
        is_sandbox=True,
        sandbox_expires_at=now + LIFETIME,
        join_code=code,
        # Twice the chance for someone who has not spoken: the default policy,
        # and the one worth showing off.
        spoken_weight=0.5,
        # A demo is for trying things, the phone microphone included; the
        # console switches it off and on (SandboxViews.microphone).
        classroom_microphone=True,
    )

    presets.apply_preset(course, presets.COVERAGE_AND_VOLUME)

    CourseMember.objects.create(
        course=course, user=owner, role="instructor", active=True,
        # A temporary instructor from the sign-in page belongs to this class
        # alone and is deleted with it, exactly like a student who joined.
        is_sandbox_guest=guest_owner,
        # The instructor's raised hand is what "the queue is open" means, so
        # the demo opens with all four already open. Otherwise the first thing
        # anyone does is wonder why the buttons do nothing.
        hand_up=True, hand_up_list_2=True, hand_up_list_3=True,
        hand_up_list_4=True,
    )

    # A lecture happening now: a session whose window contains this moment,
    # and an unended Lecture row, which is what `currentLecture` looks for.
    local = timezone.localtime(now)
    CourseSession.objects.create(
        course=course,
        is_template=False,
        name="Demonstration lecture",
        session_date=local.date(),
        start_time_pst=(local - timedelta(minutes=5)).time(),
        end_time_pst=(local + LIFETIME).time(),
    )
    Lecture.objects.create(course=course)

    return course


def session_of(course):
    """The demonstration lecture's session."""
    return course.sessions.filter(is_template=False).order_by("-id").first()


def expire_due(now=None):
    """Remove every sandbox past its expiry, and everything it owns.

    Deliberately not tied to logging out. The old one-time session deleted its
    course only if the person pressed Log out, so a closed tab left an account
    and a course behind permanently.
    """
    now = now or timezone.now()
    removed = []
    due = Course.objects.filter(
        is_sandbox=True, sandbox_expires_at__lte=now
    )
    for course in list(due):
        removed.append(course.displayname)
        remove(course)
    return removed


@transaction.atomic
def remove(course):
    """Delete one sandbox and the accounts that joined it by code."""
    if not course.is_sandbox:
        raise ValueError(f"{course.displayname!r} is not a sandbox")
    users = list(
        User.objects.filter(
            memberships__course=course,
            memberships__is_sandbox_guest=True,
        ).distinct()
    )
    course.delete()
    for user in users:
        # Only ever an account this sandbox invented. A real instructor who
        # made the sandbox keeps their account.
        if not user.memberships.exists():
            user.delete()


def for_code(code, *, now=None):
    """The live sandbox a join code belongs to, or None."""
    now = now or timezone.now()
    digits = "".join(character for character in str(code or "") if character.isdigit())
    if len(digits) != CODE_LENGTH:
        return None
    return Course.objects.filter(
        is_sandbox=True, join_code=digits, sandbox_expires_at__gt=now
    ).first()


def raised_hands(course):
    """Who has a hand up, for the console's own display."""
    return list(
        CourseMember.objects.filter(course=course, role="student", active=True)
        .filter(hand_up=True)
        .select_related("user")
        .order_by("user__first_name")
    )
