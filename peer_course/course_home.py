"""What a course's own page needs to show, for whoever is looking at it.

Agora lost its course page in the hardening commit, and with it the only route
to several features: the roster, the exports it links to, session management,
and -- since the participation charts were added -- a student's own standing.
Those views all still work; nothing linked to them.

This assembles the page rather than restoring the old one. The old page put
every control in one column regardless of who was reading, so a student saw an
instructor's export buttons greyed out. Here the same view answers a different
question depending on the reader: a student asks "how am I doing", staff ask
"what is the state of my course".
"""

from django.db.models import Count, Q
from django.utils import timezone

from . import participation_schemes as schemes
from . import participation_standing as standing
from . import selection_policy
from .models import Course, CourseMember, CourseSession
from .sessions import active_session_for_course


def next_session(course, *, at=None):
    """The session in progress, else the next one scheduled, else None."""
    # Without `manual_active`: that flag means "this course already has a
    # lecture open, so its latest unended session is authoritative", which
    # would report a lecture three days away as running now.
    live = active_session_for_course(course, at=at)
    if live is not None:
        return live, True
    now = timezone.localtime(at or timezone.now())
    upcoming = (
        CourseSession.objects.filter(
            course=course, is_template=False, ended=False,
            session_date__gte=now.date(),
        )
        .order_by("session_date", "start_time_pst")
        .first()
    )
    return upcoming, False


def roster_counts(course):
    """How many people hold each role, in one query."""
    counts = CourseMember.objects.filter(course=course, active=True).aggregate(
        students=Count("id", filter=Q(role="student")),
        tas=Count("id", filter=Q(role="ta")),
        instructors=Count("id", filter=Q(role__in=("instructor", "superuser"))),
    )
    counts["total"] = sum(counts.values())
    return counts


def class_participation(course):
    """A one-line summary of how the class is doing, or None.

    Staff asked for "some overall data about the course". The useful shape is
    not a total -- it is whether participation is spread or concentrated, which
    is what the median against the maximum says at a glance.
    """
    scheme = getattr(course, "participation_scheme", None)
    if scheme is None:
        return None
    values = schemes.measure_values(course, scheme)
    volumes = sorted(
        value[scheme.VOLUME] for value in values.values()
    )
    if not volumes:
        return None
    took_part = [v for v in volumes if v > 0]
    middle = len(volumes) // 2
    median = (
        volumes[middle]
        if len(volumes) % 2
        else (volumes[middle - 1] + volumes[middle]) / 2
    )
    return {
        "students": len(volumes),
        "took_part": len(took_part),
        "median": median,
        "maximum": volumes[-1] if volumes else 0,
        "lectures": len(schemes.lecture_scores(course, scheme)),
    }


def context_for(course, member, user):
    """Everything the course page shows, shaped by who is reading it."""
    session, in_progress = next_session(course)
    is_staff = bool(member and member.is_staff())
    is_instructor = bool(member and member.is_instructor())

    context = {
        "course": course,
        "coursemember": member,
        "is_staff": is_staff,
        "is_instructor": is_instructor,
        "next_session": session,
        "session_in_progress": in_progress,
        # Same sentences the settings page shows the instructor, from the same
        # settings the selection code reads.
        "selection_policy": selection_policy.describe(course),
    }

    if is_staff:
        context["roster"] = roster_counts(course)
        context["class_participation"] = class_participation(course)
        context["upcoming"] = list(
            CourseSession.objects.filter(
                course=course, is_template=False, ended=False,
                session_date__gte=timezone.localtime().date(),
            ).order_by("session_date", "start_time_pst")[:5]
        )
    elif member is not None:
        # A student's own standing, which had no route to it at all.
        context["standing"] = (
            standing.standing_for(course, member)
            if course.show_participation_grade
            else None
        )
    return context
