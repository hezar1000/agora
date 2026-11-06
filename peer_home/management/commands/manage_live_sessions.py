import logging
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from peer_course import hand_events, lecture_guests
from peer_course.models import Course, SchedulerLease
from peer_course.sessions import course_timezone, latest_unended_session
from peer_home.views import HomeViews
from peer_lecture.models import Lecture


logger = logging.getLogger(__name__)
LEASE_NAME = "agora-live-session-maintenance"
LEASE_DURATION = timedelta(minutes=10)
MAX_LECTURE_AGE = timedelta(hours=6)
WEEKDAYS = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}


def _acquire_lease(owner):
    now = timezone.now()
    for attempt in range(2):
        try:
            with transaction.atomic():
                lease, created = SchedulerLease.objects.select_for_update().get_or_create(
                    name=LEASE_NAME,
                    defaults={"owner": owner, "expires_at": now + LEASE_DURATION},
                )
                if not created and lease.expires_at > now:
                    return False
                if not created:
                    lease.owner = owner
                    lease.expires_at = now + LEASE_DURATION
                    lease.save(update_fields=["owner", "expires_at", "updated_at"])
                return True
        except IntegrityError:
            if attempt:
                raise
    return False


def _release_lease(owner):
    SchedulerLease.objects.filter(name=LEASE_NAME, owner=owner).update(
        expires_at=timezone.now()
    )


def run_live_session_maintenance():
    owner = uuid.uuid4().hex
    if not _acquire_lease(owner):
        logger.info("Live-session maintenance skipped because another runner holds the lease")
        return False

    try:
        failed_courses = 0
        open_course_ids = Lecture.objects.filter(end_time__isnull=True).values(
            "course_id"
        )
        courses = Course.objects.filter(
            Q(auto_start=True) | Q(pk__in=open_course_ids)
        ).distinct()
        for course in courses.iterator():
            try:
                course_tz = course_timezone(course)
                local_now = timezone.localtime(timezone.now(), course_tz)
                session_active = HomeViews.maintain_scheduled_sessions(
                    course,
                    local_now.date(),
                    local_now.time(),
                    WEEKDAYS[local_now.weekday()],
                )
                HomeViews.manage_lecture(
                    request=None,
                    course=course,
                    session_active=session_active,
                    current_date=local_now.date(),
                )
            except Exception:
                failed_courses += 1
                logger.exception(
                    "Live-session maintenance failed for a course",
                    extra={"course_id": course.id},
                )

        # Standalone/legacy lectures keep the six-hour safety cap. A scheduled
        # lecture has its own bounded deadline; counting its early setup time
        # toward that cap would split a class that was opened in the morning.
        # Close the related grading session and manual-mode state together.
        # A bulk Lecture update alone leaves the dashboard inconsistent.
        stale_before = timezone.now() - MAX_LECTURE_AGE
        stale_course_ids = list(
            Lecture.objects.filter(
                end_time__isnull=True,
                start_time__lt=stale_before,
            ).filter(
                Q(scheduled_end_time__isnull=True)
                | Q(scheduled_end_time__lte=timezone.now())
            ).values_list("course_id", flat=True)
        )
        for course_id in stale_course_ids:
            try:
                with transaction.atomic():
                    course = Course.objects.select_for_update().get(pk=course_id)
                    lecture = Lecture.objects.select_for_update().filter(
                        course=course,
                        end_time__isnull=True,
                        start_time__lt=stale_before,
                    ).filter(
                        Q(scheduled_end_time__isnull=True)
                        | Q(scheduled_end_time__lte=timezone.now())
                    ).first()
                    if lecture is None:
                        continue
                    ended_at = timezone.now()
                    Lecture.endLecture(lecture.pk, ended_at)
                    if lecture.session_id is not None:
                        open_session = course.sessions.select_for_update().filter(
                            pk=lecture.session_id, ended=False,
                        ).first()
                    else:
                        open_session = latest_unended_session(course, for_update=True)
                    if open_session is not None:
                        open_session.ended = True
                        open_session.actual_end_time = timezone.localtime(
                            ended_at,
                            course_timezone(course),
                        ).time().replace(tzinfo=None)
                        open_session.save(
                            update_fields=["ended", "actual_end_time"]
                        )
                    # The hard safety close is still a lecture boundary.  A
                    # hand left up here must not leak into the next class.
                    hand_events.clear_student_hands(course)
                    course.manual_override = False
                    course.manually_started = False
                    course.save(
                        update_fields=["manual_override", "manually_started"]
                    )
            except Exception:
                failed_courses += 1
                logger.exception(
                    "Stale live-session cleanup failed for a course",
                    extra={"course_id": course_id},
                )
        try:
            # Guests leave with their lecture (Lecture.save); this catches any
            # lecture closed some other way, and one left open past a day.
            lecture_guests.sweep()
        except Exception:
            failed_courses += 1
            logger.exception("Lecture guest sweep failed")
        if failed_courses:
            raise CommandError(
                f"Live-session maintenance failed for {failed_courses} course(s)."
            )
        return True
    finally:
        _release_lease(owner)


class Command(BaseCommand):
    help = "Start/end scheduled Agora sessions once; safe for a one-minute system timer."

    def handle(self, *args, **options):
        if run_live_session_maintenance():
            self.stdout.write(self.style.SUCCESS("Live-session maintenance completed."))
        else:
            self.stdout.write("Live-session maintenance is already running elsewhere.")
