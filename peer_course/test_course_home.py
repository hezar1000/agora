"""The course page, and the routes that had none."""

import re
from datetime import date, time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from peer_course import course_home, participation_presets as presets
from peer_course.models import Course, CourseMember, CourseSession, HandRaise
from peer_course.participation_testing import record_contributions


class CourseHomeTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Home", browsable=True, archived=False,
            agora_mode=True, show_participation_grade=True,
        )
        today = timezone.localtime().date()
        self.past = CourseSession.objects.create(
            course=self.course, session_date=today - timedelta(days=7),
            start_time_pst=time(10, 0), end_time_pst=time(11, 0),
            is_template=False, ended=True,
        )
        self.soon = CourseSession.objects.create(
            course=self.course, session_date=today + timedelta(days=2),
            start_time_pst=time(10, 0), end_time_pst=time(11, 0),
            is_template=False,
        )

    def member(self, username, role):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role=role, active=True
        )

    # -- what everyone sees ------------------------------------------------

    def test_the_next_lecture_is_the_one_coming_up(self):
        session, in_progress = course_home.next_session(self.course)
        self.assertEqual(session, self.soon)
        self.assertFalse(in_progress)

    def test_a_course_with_nothing_scheduled_says_so(self):
        CourseSession.objects.all().delete()
        session, _ = course_home.next_session(self.course)
        self.assertIsNone(session)

    # -- staff ------------------------------------------------------------

    def test_staff_get_the_roster_counts(self):
        self.member("prof", "instructor")
        self.member("ta", "ta")
        for index in range(3):
            self.member(f"s{index}", "student")

        context = course_home.context_for(
            self.course, CourseMember.objects.get(user__username="prof"), None
        )

        self.assertEqual(context["roster"]["students"], 3)
        self.assertEqual(context["roster"]["instructors"], 1)
        self.assertEqual(context["roster"]["tas"], 1)

    def test_a_course_superuser_counts_as_an_instructor_here_too(self):
        boss = self.member("boss", "superuser")
        context = course_home.context_for(self.course, boss, None)
        self.assertTrue(context["is_staff"])
        self.assertTrue(context["is_instructor"])
        self.assertEqual(context["roster"]["instructors"], 1)

    def test_a_ta_sees_the_course_but_not_the_settings(self):
        ta = self.member("ta", "ta")
        context = course_home.context_for(self.course, ta, None)
        self.assertTrue(context["is_staff"])
        self.assertFalse(context["is_instructor"])

    def test_class_participation_summarises_the_spread(self):
        presets.apply_preset(self.course, presets.VOLUME)
        self.course.refresh_from_db()
        quiet = self.member("quiet", "student")
        loud = self.member("loud", "student")
        for member, times in ((quiet, 1), (loud, 9)):
            record_contributions(member, self.past, times)

        summary = course_home.class_participation(self.course)

        self.assertEqual(summary["students"], 2)
        self.assertEqual(summary["took_part"], 2)
        self.assertEqual(summary["maximum"], 9)

    def test_a_course_without_a_scheme_has_no_participation_summary(self):
        self.assertIsNone(course_home.class_participation(self.course))

    # -- students ----------------------------------------------------------

    def test_a_student_gets_their_own_standing_not_the_roster(self):
        presets.apply_preset(self.course, presets.VOLUME)
        self.course.refresh_from_db()
        student = self.member("student", "student")

        context = course_home.context_for(self.course, student, None)

        self.assertNotIn("roster", context)
        self.assertIn("standing", context)

    def test_a_course_that_hides_grades_shows_a_student_nothing(self):
        presets.apply_preset(self.course, presets.VOLUME)
        self.course.show_participation_grade = False
        self.course.save(update_fields=["show_participation_grade"])
        self.course.refresh_from_db()
        student = self.member("student", "student")

        self.assertIsNone(
            course_home.context_for(self.course, student, None)["standing"]
        )


class ReachabilityTests(TestCase):
    """Several features worked but nothing linked to them.

    The hardening commit removed Agora's course page, and with it the only
    route to the roster -- and therefore to the roster's exports and to each
    student's participation.
    """

    def page(self, name):
        for app in ("peer_course", "peer_account", "peer_home"):
            path = Path(__file__).resolve().parent.parent / app / "templates" / name
            if path.exists():
                return path.read_text()
        raise AssertionError(f"{name} not found")

    def test_the_course_page_reaches_the_roster(self):
        self.assertIn("list_users/", self.page("course-home.html"))

    def test_the_course_page_reaches_the_settings(self):
        source = self.page("course-home.html")
        self.assertIn("course:edit", source)

    def test_the_roster_reaches_each_person(self):
        # This was already true; it was the roster itself that was unreachable.
        self.assertIn(
            '/course/{{course.id}}/{{user.id}}/', self.page("user-list-for-course.html")
        )

    def test_a_student_can_reach_their_own_participation(self):
        self.assertIn("View details", self.page("course-home.html"))

    def test_the_navigation_offers_the_course_page(self):
        self.assertIn(
            '/course/{{ request.session.course_id }}/', self.page("base.html")
        )
