"""Starting a lecture by hand runs the lecture that was scheduled.

CPSC 430 entered its term in advance -- "Class 1: Introduction", "Class 2:
..." -- and marked the first class as not counting toward participation. Its
instructor then started each lecture from the dashboard two or three minutes
before the scheduled time. The start button created a fresh, unnamed,
three-hour session each time; every hand raise landed there, and the named
sessions with their settings sat unused. The first class counted after all.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytz
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from peer_course.models import Course, CourseMember, CourseParticipation, CourseSession, HandRaise
from peer_course.sessions import active_session_for_course, scheduled_session_to_adopt
from peer_home.management.commands.manage_live_sessions import run_live_session_maintenance
from peer_lecture.models import Lecture

IN_MEMORY_CHANNELS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS, ALLOWED_HOSTS=["*", "testserver"])
class ManualStartTests(TestCase):
    def setUp(self):
        self.clock = self.enterContext(patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 10, 9, 20, 0, tzinfo=dt_timezone.utc),
        ))
        self.course = Course.objects.create(
            displayname="Scheduled in advance", browsable=True, archived=False,
            course_timezone="America/Vancouver",
            auto_start=True, default_session_duration=timedelta(minutes=90),
        )
        self.instructor = User.objects.create_user("prof", password="pw")
        CourseMember.objects.create(
            course=self.course, user=self.instructor, role="instructor"
        )
        self.client.force_login(self.instructor)
        session = self.client.session
        session["course_id"] = self.course.pk
        session.save()
        self.url = reverse("home:polling_instructor")

    def now(self):
        return timezone.localtime(
            timezone.now(), pytz.timezone(self.course.course_timezone)
        )

    def schedule(self, starts_in, lasts=timedelta(minutes=90), **fields):
        start = self.now() + starts_in
        return CourseSession.objects.create(
            course=self.course, is_template=False,
            session_date=start.date(),
            start_time_pst=start.time().replace(tzinfo=None),
            end_time_pst=(start + lasts).time().replace(tzinfo=None),
            **fields,
        )

    def start(self):
        response = self.client.post(self.url, {"toggle-lecture": "start"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["begin"])
        return response

    def end(self):
        response = self.client.post(self.url, {"toggle-lecture": "end"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["begin"])
        return response

    def real_sessions(self):
        return CourseSession.objects.filter(course=self.course, is_template=False)

    def test_a_lecture_about_to_start_is_adopted_rather_than_duplicated(self):
        scheduled = self.schedule(timedelta(minutes=3), name="Class 2")

        self.start()

        self.assertEqual(
            CourseSession.objects.filter(course=self.course, is_template=False).count(),
            1,
        )
        self.assertEqual(scheduled_session_to_adopt(self.course), scheduled)

    def test_a_lecture_already_in_its_window_is_adopted(self):
        self.schedule(-timedelta(minutes=10), name="Class 3")

        self.start()

        self.assertEqual(
            CourseSession.objects.filter(course=self.course, is_template=False).count(),
            1,
        )

    def test_without_a_scheduled_lecture_a_session_is_created(self):
        self.start()

        self.assertEqual(
            CourseSession.objects.filter(course=self.course, is_template=False).count(),
            1,
        )

    def test_a_later_lecture_today_is_adopted_for_an_early_test(self):
        scheduled = self.schedule(timedelta(hours=4), name="Tonight")

        self.start()

        self.assertEqual(
            CourseSession.objects.filter(course=self.course, is_template=False).count(),
            1,
        )
        self.assertEqual(Lecture.currentLecture(self.course.pk).session_id, scheduled.pk)

    def test_tomorrows_lecture_is_not_adopted(self):
        self.schedule(timedelta(days=1), name="Tomorrow")
        self.start()
        self.assertEqual(CourseSession.objects.filter(course=self.course, is_template=False).count(), 2)
        self.assertIsNone(Lecture.currentLecture(self.course.pk).scheduled_end_time)


    def test_a_cancelled_lecture_is_not_adopted(self):
        self.schedule(-timedelta(minutes=4), cancelled=True)

        self.assertIsNone(scheduled_session_to_adopt(self.course))

    def test_a_lecture_whose_scheduled_end_has_passed_is_not_adopted(self):
        self.schedule(-timedelta(hours=2), ended=True)

        self.assertIsNone(scheduled_session_to_adopt(self.course))

    def test_a_stopped_lecture_inside_its_window_is_adopted(self):
        stopped = self.schedule(-timedelta(minutes=5), ended=True)

        self.assertEqual(scheduled_session_to_adopt(self.course), stopped)

    def test_stop_and_restart_within_the_scheduled_time_is_one_lecture(self):
        scheduled = self.schedule(-timedelta(minutes=30), name="Lecture 7")
        self.start()
        self.end()
        scheduled.refresh_from_db()
        self.assertTrue(scheduled.ended)

        self.start()

        scheduled.refresh_from_db()
        self.assertEqual(list(self.real_sessions()), [scheduled])
        self.assertFalse(scheduled.ended)
        self.assertIsNone(scheduled.actual_end_time)
        lecture = Lecture.currentLecture(self.course.pk)
        self.assertEqual(lecture.session_id, scheduled.pk)
        self.assertEqual(
            lecture.scheduled_end_time,
            datetime(2026, 10, 9, 21, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(active_session_for_course(self.course, manual_active=True), scheduled)

    def test_restart_of_an_automatically_started_lecture_is_one_lecture(self):
        # AI 100 on 2 October: the scheduler started the lecture, a stale
        # "Start Lecture" press ended it, and the next press started another.
        scheduled = self.schedule(-timedelta(minutes=30), name="Lecture 7")
        Lecture.create(self.course.pk)
        self.end()
        self.start()
        self.end()
        self.start()

        scheduled.refresh_from_db()
        self.assertEqual(list(self.real_sessions()), [scheduled])
        self.assertFalse(scheduled.ended)
        self.assertEqual(Lecture.currentLecture(self.course.pk).session_id, scheduled.pk)

    def test_restart_after_the_scheduled_end_is_a_new_session(self):
        scheduled = self.schedule(-timedelta(hours=2), name="Earlier", ended=True)

        self.start()

        scheduled.refresh_from_db()
        self.assertTrue(scheduled.ended)
        self.assertEqual(self.real_sessions().count(), 2)
        self.assertNotEqual(Lecture.currentLecture(self.course.pk).session_id, scheduled.pk)

    def test_start_while_a_lecture_is_running_changes_nothing(self):
        scheduled = self.schedule(-timedelta(minutes=10), name="Running")
        self.start()
        lecture = Lecture.currentLecture(self.course.pk)

        response = self.start()

        self.assertEqual(response.json()["lecture_id"], lecture.pk)
        lecture.refresh_from_db(); scheduled.refresh_from_db()
        self.assertIsNone(lecture.end_time)
        self.assertFalse(scheduled.ended)
        self.assertEqual(Lecture.objects.filter(course=self.course).count(), 1)

    def test_end_without_a_running_lecture_changes_nothing(self):
        scheduled = self.schedule(-timedelta(minutes=10), name="Not started")

        response = self.end()

        self.assertIsNone(response.json()["lecture_id"])
        scheduled.refresh_from_db()
        self.assertFalse(scheduled.ended)
        self.assertFalse(Lecture.objects.filter(course=self.course).exists())

    def test_a_request_without_start_or_end_is_rejected(self):
        response = self.client.post(self.url, {"toggle-lecture": "true"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Lecture.objects.filter(course=self.course).exists())

    def test_status_reports_whether_a_lecture_is_running(self):
        self.schedule(-timedelta(minutes=10), name="Running")
        self.assertFalse(self.client.get("/check_status/").json()["lecture_running"])
        self.start()
        self.assertTrue(self.client.get("/check_status/").json()["lecture_running"])

    def test_ending_the_lecture_ends_the_adopted_session(self):
        scheduled = self.schedule(timedelta(minutes=2), name="Class 4")
        self.start()

        self.end()

        scheduled.refresh_from_db()
        self.assertTrue(scheduled.ended)
        self.assertFalse(
            Lecture.objects.filter(course=self.course, end_time__isnull=True).exists()
        )

    def move_clock(self, hour, minute=0):
        self.clock.return_value = datetime(2026, 10, 9, hour + 7, minute, tzinfo=dt_timezone.utc)

    def test_early_audio_test_stays_in_one_lecture_through_scheduled_end(self):
        scheduled = self.schedule(timedelta(hours=1), name="Lecture 9: Alignment")
        self.start()
        lecture = Lecture.currentLecture(self.course.pk)
        student = CourseMember.objects.create(
            course=self.course, role="student", user=User.objects.create_user("audio-test-student"),
        )
        student.hand_up = True
        student.save(update_fields=["hand_up"])
        hand = HandRaise.objects.get(member=student, participation_list=1, resolved_at__isnull=True)
        self.assertEqual(hand.session_id, scheduled.pk)
        participation = CourseParticipation.objects.create(participant=student, session=scheduled, participation_points_gained=3)
        self.assertEqual(lecture.scheduled_end_time, datetime(2026, 10, 9, 22, 30, tzinfo=dt_timezone.utc))
        self.assertEqual(active_session_for_course(self.course, manual_active=True), scheduled)

        for hour, minute in ((14, 0), (14, 31), (14, 32), (15, 29)):
            with self.subTest(hour=hour, minute=minute):
                self.move_clock(hour, minute)
                self.assertTrue(run_live_session_maintenance())
                lecture.refresh_from_db(); scheduled.refresh_from_db(); hand.refresh_from_db()
                self.assertIsNone(lecture.end_time)
                self.assertFalse(scheduled.ended)
                self.assertIsNone(hand.resolved_at)
                self.assertEqual(Lecture.currentLecture(self.course.pk).pk, lecture.pk)
                self.assertEqual(active_session_for_course(self.course, manual_active=True), scheduled)
                self.assertEqual(CourseSession.objects.filter(course=self.course, is_template=False).count(), 1)

        self.move_clock(15, 31)
        self.assertTrue(run_live_session_maintenance())
        self.move_clock(15, 32)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db(); scheduled.refresh_from_db(); hand.refresh_from_db(); participation.refresh_from_db()
        self.assertIsNotNone(lecture.end_time)
        self.assertTrue(scheduled.ended)
        self.assertIsNotNone(hand.resolved_at)
        self.assertEqual(Lecture.objects.filter(course=self.course).count(), 1)
        self.assertEqual(participation.session_id, scheduled.pk)
        self.assertEqual(participation.participation_points_gained, 3)

    def test_adopted_session_does_not_switch_to_todays_newer_later_lecture(self):
        current = self.schedule(timedelta(hours=1), name="First")
        later = self.schedule(timedelta(hours=4), name="Second")
        self.start()
        self.assertEqual(Lecture.currentLecture(self.course.pk).session_id, current.pk)
        self.assertEqual(active_session_for_course(self.course, manual_active=True), current)
        later.refresh_from_db()
        self.assertFalse(later.ended)

    def test_running_lecture_is_preferred_over_a_nearby_future_one(self):
        current = self.schedule(-timedelta(minutes=20), name="Running")
        self.schedule(timedelta(minutes=1), name="Next")
        self.start()
        self.assertEqual(Lecture.currentLecture(self.course.pk).session_id, current.pk)

    def test_changing_grading_date_does_not_shorten_scheduled_deadline(self):
        scheduled = self.schedule(timedelta(hours=1), name="Scheduled")
        self.start()
        lecture = Lecture.currentLecture(self.course.pk)
        scheduled.session_date -= timedelta(days=1)
        scheduled.save(update_fields=["session_date"])
        self.move_clock(14, 31)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db()
        self.assertIsNone(lecture.end_time)
        self.assertEqual(active_session_for_course(self.course, manual_active=True), scheduled)
        self.move_clock(15, 31)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db()
        self.assertIsNotNone(lecture.end_time)

    def test_morning_setup_does_not_trigger_six_hour_cutoff_during_class(self):
        self.move_clock(8)
        scheduled = self.schedule(timedelta(hours=6), name="Afternoon")
        self.start()
        lecture = Lecture.currentLecture(self.course.pk)
        self.move_clock(14, 31)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db(); scheduled.refresh_from_db()
        self.assertIsNone(lecture.end_time)
        self.assertFalse(scheduled.ended)
        self.move_clock(15, 31)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db()
        self.assertIsNotNone(lecture.end_time)

    def test_unscheduled_manual_lecture_still_expires_after_its_duration(self):
        self.start()
        lecture = Lecture.currentLecture(self.course.pk)
        self.assertIsNone(lecture.scheduled_end_time)
        self.move_clock(14, 31)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db()
        self.assertIsNotNone(lecture.end_time)
        self.assertFalse(Lecture.objects.filter(course=self.course, end_time__isnull=True).exists())

    def test_previous_nights_overnight_lecture_keeps_its_scheduled_end(self):
        self.move_clock(0)
        now = self.now()
        start = now - timedelta(hours=1)
        scheduled = CourseSession.objects.create(
            course=self.course, session_date=start.date(), start_time_pst=start.time().replace(tzinfo=None),
            end_time_pst=(now + timedelta(hours=1)).time().replace(tzinfo=None), name="Overnight",
        )
        self.start()
        lecture = Lecture.currentLecture(self.course.pk)
        self.assertEqual(lecture.session_id, scheduled.pk)
        self.move_clock(0, 45)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db()
        self.assertIsNone(lecture.end_time)
        self.move_clock(1, 1)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db()
        self.assertIsNotNone(lecture.end_time)

    def test_cancelled_linked_session_does_not_switch_to_or_end_later_class(self):
        scheduled = self.schedule(timedelta(hours=1), name="Cancelled")
        later = self.schedule(timedelta(hours=4), name="Later")
        self.start()
        lecture = Lecture.currentLecture(self.course.pk)
        scheduled.cancelled = True
        scheduled.save(update_fields=["cancelled"])
        self.assertIsNone(active_session_for_course(self.course, manual_active=True))
        self.move_clock(15, 31)
        self.assertTrue(run_live_session_maintenance())
        lecture.refresh_from_db(); later.refresh_from_db()
        self.assertIsNotNone(lecture.end_time)
        self.assertFalse(later.ended)
