from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from .base import CourseBase
from .models import Course, CourseMember

# Test scenario 1: add new course
# Test scenario 2: enter with instructor/ta/student code as well as wrong codes

# Columns Agora inherited from MTA's peer-review schema. Migration
# peer_course.0093 drops them; nothing may bring them back.
MTA_COURSE_COLUMNS = {
    "show_student_grades",
    "on_list",
    "total_late_units",
    "can_tas_see_reviews",
    "enable_independent_pool",
    "agora_mode",
}
MTA_MEMBER_COLUMNS = {
    "is_independent",
    "time_is_independent_changed",
    "qualified",
    "upperconfidencebound",
    "markingload",
    "lowerconfidencebound",
    "regular_points",
}


class AgoraProductBoundaryTests(TestCase):
    def test_mta_runtime_helpers_are_absent(self):
        self.assertFalse(hasattr(CourseBase, "is_independent"))
        self.assertFalse(hasattr(CourseBase, "get_graders"))
        with self.assertRaises(NoReverseMatch):
            reverse("home:disqualify_all")

    def test_mta_columns_are_gone_from_the_schema(self):
        with connection.cursor() as cursor:
            course_columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, Course._meta.db_table
                )
            }
            member_columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, CourseMember._meta.db_table
                )
            }

        self.assertTrue(MTA_COURSE_COLUMNS.isdisjoint(course_columns))
        self.assertTrue(MTA_MEMBER_COLUMNS.isdisjoint(member_columns))


class CourseMembershipMutationTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(username="instructor")
        self.student = User.objects.create_user(username="student")
        self.course = Course.objects.create(
            displayname="Secure course",
            browsable=False,
            archived=False,
            stucode="student-code-value-1234567890",
            tascode="ta-code-value-12345678901234",
            instructor_code="instructor-code-value-123456",
        )
        CourseMember.objects.create(
            course=self.course,
            user=self.instructor,
            role="instructor",
        )
        self.student_membership = CourseMember.objects.create(
            course=self.course,
            user=self.student,
            role="student",
        )
        self.client.force_login(self.instructor)
        session = self.client.session
        session["course_id"] = self.course.id
        session.save()
        self.remove_url = (
            f"/course/{self.course.id}/remove_user/{self.student.id}/"
        )

    def test_membership_page_uses_csrf_protected_post_form(self):
        response = self.client.get(f"/course/{self.course.id}/list_users/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'action="{self.remove_url}"')
        self.assertContains(response, 'method="post"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_remove_user_rejects_get_and_accepts_post(self):
        response = self.client.get(self.remove_url)

        self.assertEqual(response.status_code, 405)
        self.student_membership.refresh_from_db()
        self.assertTrue(self.student_membership.active)

        response = self.client.post(self.remove_url)

        self.assertEqual(response.status_code, 302)
        self.student_membership.refresh_from_db()
        self.assertFalse(self.student_membership.active)


class ActiveInstructorPermissionTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(username="revocable-instructor")
        self.course = Course.objects.create(
            displayname="Instructor status course",
            browsable=False,
            archived=False,
            stucode="student-code-value-1234567890",
            tascode="ta-code-value-12345678901234",
            instructor_code="instructor-code-value-123456",
        )
        self.membership = CourseMember.objects.create(
            course=self.course,
            user=self.instructor,
            role="instructor",
            active=True,
        )

    def test_active_instructor_is_recognized(self):
        self.assertTrue(CourseBase.is_instructor_some_course(self.instructor))

    def test_revoked_instructor_is_not_recognized(self):
        self.membership.active = False
        self.membership.save(update_fields=["active"])

        self.assertFalse(CourseBase.is_instructor_some_course(self.instructor))


class SelectedCourseRecoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="course-recovery-user")
        self.course = Course.objects.create(
            displayname="Recoverable selection",
            browsable=False,
            archived=False,
            stucode="student-code-value-1234567890",
            tascode="ta-code-value-12345678901234",
            instructor_code="instructor-code-value-123456",
        )
        self.membership = CourseMember.objects.create(
            course=self.course,
            user=self.user,
            role="student",
            active=True,
        )
        self.client.force_login(self.user)

    def _select_course(self, course_id=None):
        session = self.client.session
        session["course_id"] = course_id or self.course.pk
        session["course_role"] = "student"
        session.save()

    def test_course_list_keeps_valid_active_selection(self):
        self._select_course()

        response = self.client.get(reverse("course:list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get("course_id"), self.course.pk)

    def test_course_list_clears_deleted_selection(self):
        stale_id = self.course.pk
        self._select_course(stale_id)
        self.course.delete()

        response = self.client.get(reverse("course:list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("course_id", self.client.session)
        self.assertNotIn("course_role", self.client.session)

    def test_course_list_clears_revoked_membership_selection(self):
        self._select_course()
        self.membership.active = False
        self.membership.save(update_fields=["active"])

        response = self.client.get(reverse("course:list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("course_id", self.client.session)
        self.assertNotIn("course_role", self.client.session)
