"""Timezone-aware resolution of the real Agora session that is active now."""

from datetime import datetime, timedelta

import pytz
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from peer_course.models import (
    AttendanceCheck,
    CourseParticipation,
    CourseSession,
    HandRaise,
)


def course_timezone(course):
    """Return the configured course timezone, falling back safely for old rows."""
    try:
        return pytz.timezone(course.course_timezone)
    except (pytz.UnknownTimeZoneError, AttributeError, TypeError):
        return pytz.UTC


def session_bounds(session, tz=None):
    """Return aware start/end datetimes, including sessions crossing midnight."""
    if not session.session_date or not session.start_time_pst or not session.end_time_pst:
        return None
    tz = tz or course_timezone(session.course)
    start = tz.localize(datetime.combine(session.session_date, session.start_time_pst))
    end_date = session.session_date
    if session.end_time_pst <= session.start_time_pst:
        end_date += timedelta(days=1)
    end = tz.localize(datetime.combine(end_date, session.end_time_pst))
    return start, end


def active_session_for_course(course, *, at=None, for_update=False, manual_active=False):
    """Resolve one active real session consistently for every live Agora action.

    ``session_date`` is the date on which a session starts. Consequently, an
    overnight session can be active on either that date or the following date.
    A manually started lecture may intentionally use a different grading date;
    callers pass ``manual_active=True`` only while that course has an open
    lecture, in which case its latest unended real session remains authoritative.
    """
    tz = course_timezone(course)
    local_now = timezone.localtime(at or timezone.now(), tz)
    queryset = CourseSession.objects.filter(
        course=course,
        is_template=False,
        ended=False,
        # A day marked as having no lecture is not one anyone can be in.
        cancelled=False,
    )
    if for_update:
        queryset = queryset.select_for_update()

    if manual_active:
        from peer_lecture.models import Lecture

        lecture = Lecture.currentLecture(course.pk)
        if lecture is not None and lecture.session_id is not None:
            # The chosen session remains authoritative even before its start,
            # after its grading date is edited, or when today's later class
            # has a newer id. Never fall through to a different session if
            # the linked one has been ended or cancelled.
            return queryset.filter(pk=lecture.session_id).first()

        # Legacy lectures have no explicit link. Never a lecture that has not happened yet. Sessions are ended once
        # their window has passed, so among unended rows the newest id means
        # "most recent" only while future lectures do not exist yet -- which
        # was true when every session was spawned on the day it ran. A term's
        # schedule created in advance leaves all seventeen unended, and the
        # newest is December's, so every hand raised all term would be
        # recorded against the last lecture. The date tells "still open" from
        # "not yet begun"; the id cannot.
        manual_session = (
            queryset.filter(session_date__lte=local_now.date())
            .order_by("-id")
            .first()
        )
        if manual_session is not None:
            return manual_session

    candidate_dates = (local_now.date(), local_now.date() - timedelta(days=1))
    candidates = queryset.filter(session_date__in=candidate_dates)
    active = []
    for session in candidates:
        bounds = session_bounds(session, tz)
        if bounds is not None and bounds[0] <= local_now <= bounds[1]:
            active.append((bounds[0], session))
    if not active:
        return None
    return max(active, key=lambda item: (item[0], item[1].pk))[1]


def latest_unended_session(course, *, at=None, for_update=False):
    """Return the latest real session that has actually begun.

    A term can be entered in advance.  In that case ordering all unended rows
    by id chooses the final lecture of the term while the first is running.
    Lifecycle cleanup must never end a future session, so exclude rows whose
    scheduled start is still ahead of the course-local clock.
    """
    tz = course_timezone(course)
    local_now = timezone.localtime(at or timezone.now(), tz)
    local_time = local_now.time().replace(tzinfo=None)
    queryset = CourseSession.objects.filter(
        course=course,
        is_template=False,
        ended=False,
        # A day marked as having no lecture is not one anyone can be in.
        cancelled=False,
    )
    if for_update:
        queryset = queryset.select_for_update()
    return (
        queryset.filter(
            Q(session_date__lt=local_now.date())
            | Q(
                session_date=local_now.date(),
                start_time_pst__lte=local_time,
            )
            | Q(
                session_date=local_now.date(),
                start_time_pst__isnull=True,
            )
        )
        .order_by("-session_date", "-start_time_pst", "-id")
        .first()
    )


def scheduled_session_to_adopt(course, at=None):
    """The scheduled lecture a manual start should run in, or None.

    An instructor who presses "Start lecture" at 15:27 for a class scheduled
    at 15:30 means that class. Spawning a fresh session for them instead left
    a term entered in advance -- with each lecture named, and a first meeting
    marked as not counting -- entirely unused: every hand raise landed in an
    unnamed session beside the scheduled one, which then counted regardless.

    An early audio test still belongs to today's next scheduled lecture;
    there is no thirty-minute cutoff. Prefer a lecture already in progress,
    then the next one today. Yesterday's overnight lecture can be adopted
    while it is in progress.

    A lecture stopped and started again before its scheduled end is the same
    lecture, so its stopped session is returned too and the caller resumes
    it. AI 100's instructor pressed a stale "Start Lecture" button during
    class on 2 October: the press ended the running lecture, pressing again
    started a fresh one, and every student saw two lectures that day.
    Lectures whose scheduled end has passed, cancelled lectures and
    tomorrow's schedule are never adopted.
    """
    tz = course_timezone(course)
    local_now = timezone.localtime(at or timezone.now(), tz)
    candidates = CourseSession.objects.filter(
        course=course,
        is_template=False,
        cancelled=False,
        # Yesterday too, for a lecture that runs past midnight -- and so that
        # a start pressed at 00:00:30 still finds the lecture dated 23:59.
        session_date__in=(local_now.date(), local_now.date() - timedelta(days=1)),
    )
    adoptable = []
    for session in candidates:
        bounds = session_bounds(session, tz)
        if bounds is None:
            continue
        start, end = bounds
        if start <= local_now <= end:
            adoptable.append((0, -start.timestamp(), session.pk, session))
        elif start.date() == local_now.date() and local_now < start:
            adoptable.append((1, start.timestamp(), session.pk, session))
    if not adoptable:
        return None
    return min(adoptable, key=lambda item: item[:3])[3]


def held_lectures(sessions):
    """Only the sessions in which something happened.

    A lecture in which nothing at all happened -- no hand raised, nobody
    called on, no roll call -- was not a real lecture: it was never opened,
    or it was a duplicate left by starting a lecture twice. It is left out of
    the lists of lectures and the exports rather than shown as a lecture
    everybody missed.
    """
    def happened(model):
        return Exists(model.objects.filter(session=OuterRef("pk")))

    return sessions.filter(
        happened(HandRaise) | happened(CourseParticipation) | happened(AttendanceCheck)
    )
