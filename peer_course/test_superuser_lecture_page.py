"""The lecture page, opened by a superuser who is not in the course.

CourseBase.get_course_member hands a superuser an *unsaved* CourseMember so
that the rest of the page has a role to read. Django refuses to filter a
relation on an unsaved instance, so both places that asked "what has this
member done in this lecture" raised ValueError -- but only while a session
was open, because both are guarded by `if session is not None`. The page
therefore worked all morning and started failing the moment the lecture did.
"""

from datetime import time

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from peer_course import participation_presets as presets
from peer_course import participation_rules
from peer_course.base import CourseBase
from peer_course.models import Course, CourseMember, CourseSession
from peer_course.participation_testing import (
    record_contributions, recorded_credit,
)
from peer_lecture.models import Lecture


class SuperuserDuringAnOpenLectureTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="AI 100 2026W1", browsable=True, archived=False,
            agora_mode=True,
        )
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        CourseMember.objects.create(
            course=self.course, role="instructor", active=True,
            user=User.objects.create_user("the-real-instructor"),
        )
        # A lecture happening right now.
        self.session = CourseSession.objects.create(
            course=self.course, is_template=False,
            session_date=timezone.localtime(timezone.now()).date(),
            start_time_pst=time(0, 1), end_time_pst=time(23, 59),
            counts_for_participation=False,
        )
        Lecture.objects.create(course=self.course)

        self.root = User.objects.create_superuser(
            "root", "root@example.com", "x")

    def _open_the_lecture_page(self):
        client = Client()
        client.force_login(self.root)
        session = client.session
        session["course_id"] = self.course.id
        session.save()
        return client.get("/agora_dash/")

    def test_a_superuser_who_is_not_enrolled_can_open_the_page(self):
        # Before the guard this raised ValueError: Model instances passed to
        # related filters must be saved.
        self.assertEqual(self._open_the_lecture_page().status_code, 200)

    def test_it_still_works_once_the_lecture_has_ended(self):
        Lecture.objects.filter(course=self.course).update(end_time=timezone.now())
        self.session.ended = True
        self.session.save(update_fields=["ended"])

        self.assertEqual(self._open_the_lecture_page().status_code, 200)

    def test_the_status_of_an_unsaved_member_is_simply_empty(self):
        phantom = CourseBase.get_course_member(self.root, self.course.id)
        self.assertIsNone(phantom.pk)

        status = participation_rules.lecture_status(
            self.course, phantom, self.session)

        self.assertFalse(status["counted"])
        self.assertEqual(status["contributions"], 0)

    def test_an_enrolled_member_is_still_counted_normally(self):
        # The guard must not silence real members.
        student = CourseMember.objects.create(
            course=self.course, role="student", active=True,
            user=User.objects.create_user("really-here"),
        )
        # Hand up when the instructor called on someone: a contribution.
        record_contributions(student, self.session, 1)
        points, bonus = recorded_credit(student, self.session)

        status = participation_rules.lecture_status(
            self.course, student, self.session, points, bonus)

        self.assertTrue(status["counted"])
