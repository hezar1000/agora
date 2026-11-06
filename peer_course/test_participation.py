from datetime import date, time
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase

from peer_course.forms import CourseForm
from peer_course.models import Course, CourseMember, CourseParticipation, CourseSession
from peer_course.participation import (
    calculate_participation_grade,
    course_session_participation_grades,
)


class ParticipationGradeFormulaTests(TestCase):
    def test_fixed_thresholds(self):
        expected = {0: 0, 1: 5, 2: 6, 3: 6.5, 4: 7}
        for total, grade in expected.items():
            with self.subTest(total=total):
                self.assertEqual(calculate_participation_grade(total, 8, 15), grade)

    def test_interpolates_below_and_above_median(self):
        self.assertEqual(calculate_participation_grade(6, 8, 15), 7.5)
        self.assertEqual(calculate_participation_grade(11, 8, 15), 8.86)

    def test_median_and_maximum_are_thresholds(self):
        self.assertEqual(calculate_participation_grade(8, 8, 15), 8)
        self.assertEqual(calculate_participation_grade(15, 8, 15), 10)

    def test_median_at_or_below_four_overrides_fixed_threshold(self):
        self.assertEqual(calculate_participation_grade(3, 3, 8), 8)
        self.assertEqual(calculate_participation_grade(4, 4, 8), 8)

    def test_shared_median_and_maximum_gets_largest_applicable_grade(self):
        self.assertEqual(calculate_participation_grade(4, 4, 4), 10)


class CourseParticipationGradeTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Participation Grades",
            browsable=True,
            archived=False,
            agora_mode=True,
        )
        self.session_one = CourseSession.objects.create(
            course=self.course,
            session_date=date(2026, 1, 10),
            start_time_pst=time(10, 0),
            end_time_pst=time(11, 0),
            is_template=False,
        )
        self.session_two = CourseSession.objects.create(
            course=self.course,
            session_date=date(2026, 1, 12),
            start_time_pst=time(10, 0),
            end_time_pst=time(11, 0),
            is_template=False,
        )

    def make_student(self, username):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role="student"
        )

    def add_points(self, student, session, main=0, bonus=0):
        return CourseParticipation.objects.create(
            participant=student,
            session=session,
            participation_points_gained=main,
            bonus_points=bonus,
            count_in_calculations=True,
        )

    def test_each_session_has_independent_anchors_and_grades(self):
        zero = self.make_student("zero")
        low = self.make_student("low")
        high = self.make_student("high")
        bonus_only = self.make_student("bonus-only")
        self.add_points(low, self.session_one, main=3, bonus=1)
        self.add_points(high, self.session_one, main=6, bonus=2)
        self.add_points(bonus_only, self.session_one, main=0, bonus=10)
        self.add_points(low, self.session_two, main=1)
        self.add_points(high, self.session_two, main=9, bonus=1)

        sessions = course_session_participation_grades(self.course)
        first, second = sessions[0], sessions[1]

        self.assertEqual(first["median"], 4.5)
        self.assertEqual(first["maximum"], 6)
        self.assertEqual(first["students"][zero.id]["grade"], 0)
        self.assertEqual(first["students"][low.id]["total"], 4)
        self.assertEqual(first["students"][high.id]["grade"], 10)
        self.assertEqual(first["students"][bonus_only.id]["main"], 0)
        self.assertEqual(first["students"][bonus_only.id]["total"], 10)
        self.assertEqual(first["students"][bonus_only.id]["grade"], 10)

        self.assertEqual(second["median"], 5)
        self.assertEqual(second["maximum"], 9)
        self.assertEqual(second["students"][low.id]["grade"], 5)
        self.assertEqual(second["students"][high.id]["total"], 10)
        self.assertEqual(second["students"][high.id]["grade"], 10)

    def test_a_quiet_session_does_not_depress_a_full_one(self):
        """Anchors are per session, which is the point of the rubric."""
        steady = self.make_student("steady")
        self.add_points(steady, self.session_one, main=2)
        self.add_points(steady, self.session_two, main=2)

        sessions = course_session_participation_grades(self.course)

        for session in sessions:
            self.assertEqual(session["students"][steady.id]["grade"], 10)

    def test_visibility_is_an_optional_course_setting(self):
        self.assertFalse(self.course.show_participation_grade)
        self.assertIn(
            "show_participation_grade", CourseForm(instance=self.course).fields
        )

    def test_multiple_queues_is_an_optional_course_setting(self):
        self.assertFalse(self.course.allow_multiple_queues)
        self.assertIn(
            "allow_multiple_queues", CourseForm(instance=self.course).fields
        )


class MultipleQueueTemplateTests(TestCase):
    """The multiple-queue rule is enforced in the browser, from a course flag.

    A template that renders the flag without the course in context fails
    silently: every course reads as single-queue and the setting does nothing.
    """

    def _template(self):
        from pathlib import Path

        from django.conf import settings

        return (
            Path(settings.BASE_DIR)
            / "peer_course"
            / "templates"
            / "agora.html"
        ).read_text(encoding="utf-8")

    def test_the_single_queue_check_consults_the_course_setting(self):
        source = self._template()
        self.assertIn("course.allow_multiple_queues", source)
        self.assertIn("if (allowMultipleQueues) return false;", source)

    def test_the_view_puts_the_course_in_that_template_context(self):
        source = (
            Path(__file__).resolve().parent.parent
            / "peer_home"
            / "views.py"
        ).read_text(encoding="utf-8")
        self.assertIn('render_dict["course"] = course', source)
        self.assertIn('return render(request, "agora.html", render_dict)', source)

    def test_rendering_reflects_the_setting(self):
        from django.template.loader import render_to_string

        course = Course.objects.create(
            displayname="Queues", browsable=True, archived=False, agora_mode=True
        )
        for allowed, expected in ((True, "true"), (False, "false")):
            course.allow_multiple_queues = allowed
            course.save(update_fields=["allow_multiple_queues"])
            with self.subTest(allow_multiple_queues=allowed):
                from django.template import Context, Template

                rendered = Template(
                    "{{ course.allow_multiple_queues|yesno:'true,false' }}"
                ).render(Context({"course": course}))
                self.assertEqual(rendered, expected)


class ParticipationGradeDisplayTests(TestCase):
    """The instructor's participation view shows grades when the course opts in."""

    def setUp(self):
        self.instructor_user = User.objects.create_user(
            username="grade-display-instructor", password="testpass123"
        )
        self.student_user = User.objects.create_user(
            username="grade-display-student", password="testpass123"
        )
        self.course = Course.objects.create(
            displayname="Grade Display",
            browsable=True,
            archived=False,
            agora_mode=True,
        )
        CourseMember.objects.create(
            course=self.course, user=self.instructor_user, role="instructor"
        )
        self.student = CourseMember.objects.create(
            course=self.course, user=self.student_user, role="student"
        )
        self.session = CourseSession.objects.create(
            course=self.course,
            session_date=date(2026, 1, 10),
            start_time_pst=time(10, 0),
            end_time_pst=time(11, 0),
            is_template=False,
        )
        CourseParticipation.objects.create(
            participant=self.student,
            session=self.session,
            participation_points_gained=4,
            bonus_points=1,
            count_in_calculations=True,
        )
        self.client.force_login(self.instructor_user)
        browser_session = self.client.session
        browser_session["course_id"] = self.course.id
        browser_session.save()

    def _get(self):
        return self.client.get(f"/course/{self.course.id}/{self.student_user.id}/")

    def test_grades_are_hidden_until_the_course_opts_in(self):
        self.assertFalse(self.course.show_participation_grade)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["session_grades"], [])
        self.assertNotContains(response, "Grade by lecture")

    def test_grades_appear_once_the_course_opts_in(self):
        self.course.show_participation_grade = True
        self.course.save(update_fields=["show_participation_grade"])

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade by lecture")
        grades = response.context["session_grades"]
        self.assertEqual(len(grades), 1)
        # Sole participant, so their own total is both median and maximum.
        self.assertEqual(grades[0]["total"], 5)
        self.assertEqual(grades[0]["grade"], 10.0)

    def test_a_student_who_did_not_participate_is_graded_zero_not_omitted(self):
        """They still appear for the session, which is what an instructor needs."""
        self.course.show_participation_grade = True
        self.course.save(update_fields=["show_participation_grade"])
        quiet_user = User.objects.create_user(username="grade-display-quiet")
        CourseMember.objects.create(
            course=self.course, user=quiet_user, role="student"
        )

        response = self.client.get(
            f"/course/{self.course.id}/{quiet_user.id}/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["grade"] for row in response.context["session_grades"]], [0.0]
        )

    def test_a_student_can_view_their_own_participation(self):
        self.client.force_login(self.student_user)
        browser_session = self.client.session
        browser_session["course_id"] = self.course.id
        browser_session.save()

        self.assertEqual(self._get().status_code, 200)

    def test_a_student_cannot_view_a_classmates_participation(self):
        classmate = User.objects.create_user(username="grade-display-classmate")
        CourseMember.objects.create(
            course=self.course, user=classmate, role="student"
        )
        self.client.force_login(self.student_user)

        response = self.client.get(
            f"/course/{self.course.id}/{classmate.id}/"
        )

        self.assertEqual(response.status_code, 403)
