"""A term's schedule entered before it runs must not confuse anything.

Sessions used to appear on the morning they ran, so "the newest unended
session" meant "the lecture happening now" and a course's session list held
only lectures that had already happened. AI 100 enters all seventeen lectures
in September. Every assumption that rested on the old shape is checked here.
"""

import csv
import io
from datetime import date, time, timedelta
from importlib import import_module

from django.conf import settings
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from peer_course.models import Course, CourseMember, CourseSession
from peer_course.views import CourseViews


class ScheduleEnteredInAdvanceTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Entered ahead", browsable=False, archived=False,
            course_timezone="US/Pacific",
        )
        today = timezone.localdate()
        self.past = CourseSession.objects.create(
            course=self.course, is_template=False, name="Lecture 1",
            session_date=today - timedelta(days=3),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        self.last = CourseSession.objects.create(
            course=self.course, is_template=False, name="Lecture 17",
            session_date=today + timedelta(days=80),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        self.patterns = [
            CourseSession.objects.create(
                course=self.course, is_template=True, active_days=day,
                start_time_pst=time(14, 0), end_time_pst=time(15, 30),
            )
            for day in ("wed", "fri")
        ]
        self.instructor = CourseMember.objects.create(
            course=self.course, role="instructor",
            user=User.objects.create_user("ahead-instructor"),
        )
        CourseMember.objects.create(
            course=self.course, role="student",
            user=User.objects.create_user("ahead-student"),
        )

    def _request(self, path, **post):
        factory = RequestFactory()
        request = factory.post(path, post) if post else factory.get(path)
        request.user = self.instructor.user
        store = import_module(settings.SESSION_ENGINE).SessionStore
        request.session = store()
        request.session["course_id"] = self.course.id
        return request

    def test_setting_todays_date_does_not_rewrite_the_last_lecture(self):
        today = timezone.localdate()
        CourseViews.update_session_date(
            self._request(f"/course/{self.course.id}/update_session_date/",
                          session_date=today.isoformat()),
            self.course.id,
        )

        self.last.refresh_from_db()
        self.assertNotEqual(
            self.last.session_date, today,
            "December's lecture was moved to today",
        )

    def _export(self, kind):
        return CourseViews.export_participation_data_custom(
            self._request(
                f"/course/{self.course.id}/export_participation_data_custom/"
                f"?download_type={kind}"
            ),
            self.course.id,
        )

    def test_a_weekly_pattern_is_not_a_column_in_the_export(self):
        # A lecture with nothing in it is not a column at all, so give both
        # real ones something; the patterns are the question here.
        from peer_course.participation_testing import record_contributions

        student = CourseMember.objects.get(course=self.course, role="student")
        record_contributions(student, self.past)
        record_contributions(student, self.last)
        response = self._export("lectures")

        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        header = next(row for row in rows if row and row[0] == "Student name")
        lecture_columns = [cell for cell in header if cell.startswith("20")]
        self.assertEqual(
            len(lecture_columns), 2,
            "the export gave a column to a weekly pattern",
        )

    def test_a_pattern_with_no_date_does_not_break_the_export(self):
        # Patterns created through the settings page carry no date, and a
        # column header formats one for every session it is given.
        self.assertIsNone(self.patterns[0].session_date)

        for kind in ("grades", "lectures", "full"):
            with self.subTest(kind=kind):
                self.assertEqual(self._export(kind).status_code, 200)
