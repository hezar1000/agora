"""A course superuser can do everything a course instructor can.

`superuser` is a CourseMember role -- thirteen members hold it in production --
and is distinct from Django's `User.is_superuser` flag. Every check used to
compare against the literal "instructor", so a course superuser silently had
fewer privileges than the instructors they were meant to outrank, while a
`get_user_role` of "superuser" still counted as a member.
"""

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from peer_course.base import CourseBase, CoursePermissions
from peer_course.models import (
    INSTRUCTOR_ROLES,
    STAFF_ROLES,
    Course,
    CourseMember,
)


class SuperuserRoleTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Roles", browsable=True, archived=False
        )

    def member(self, username, role):
        user = User.objects.create_user(username=username)
        CourseMember.objects.create(
            course=self.course, user=user, role=role, active=True
        )
        return user

    def test_the_role_is_named_not_spelled_out(self):
        self.assertIn("superuser", INSTRUCTOR_ROLES)
        self.assertIn("instructor", INSTRUCTOR_ROLES)
        self.assertEqual(set(STAFF_ROLES), set(INSTRUCTOR_ROLES) | {"ta"})

    def test_a_course_superuser_passes_every_instructor_check(self):
        boss = self.member("boss", "superuser")
        # `superuser_fine=False` disables the Django-flag shortcut, so these
        # test the course role itself rather than a global override.
        for check in (
            CourseBase.is_instructor,
            CourseBase.is_course_staff,
            CourseBase.is_course_member,
        ):
            with self.subTest(check=check.__name__):
                self.assertTrue(check(boss, self.course.id, superuser_fine=False))

    def test_a_course_superuser_is_not_a_student(self):
        boss = self.member("boss", "superuser")
        self.assertFalse(
            CourseBase.is_student(boss, self.course.id, superuser_fine=False)
        )

    def test_require_instructor_admits_a_course_superuser(self):
        boss = self.member("boss", "superuser")
        try:
            CoursePermissions.require_instructor(boss, self.course.id, superuser_fine=False)
        except PermissionDenied:
            self.fail("a course superuser was refused an instructor page")

    def test_require_course_staff_admits_a_course_superuser(self):
        boss = self.member("boss", "superuser")
        try:
            CoursePermissions.require_course_staff(boss, self.course.id, superuser_fine=False)
        except PermissionDenied:
            self.fail("a course superuser was refused a staff page")

    def test_a_student_is_still_refused(self):
        student = self.member("student", "student")
        with self.assertRaises(PermissionDenied):
            CoursePermissions.require_instructor(
                student, self.course.id, superuser_fine=False
            )

    def test_a_ta_is_staff_but_not_an_instructor(self):
        ta = self.member("ta", "ta")
        self.assertTrue(
            CourseBase.is_course_staff(ta, self.course.id, superuser_fine=False)
        )
        self.assertFalse(
            CourseBase.is_instructor(ta, self.course.id, superuser_fine=False)
        )

    def test_the_model_helpers_agree_with_the_gates(self):
        for role, instructor, staff in (
            ("superuser", True, True),
            ("instructor", True, True),
            ("ta", False, True),
            ("student", False, False),
        ):
            with self.subTest(role=role):
                member = CourseMember(course=self.course, role=role)
                self.assertEqual(member.is_instructor(), instructor)
                self.assertEqual(member.is_staff(), staff)

    def test_course_creation_is_open_to_a_course_superuser(self):
        boss = self.member("boss", "superuser")
        self.assertTrue(
            CourseBase.is_instructor_some_course(boss, superuser_fine=False)
        )

    def test_no_privilege_check_compares_against_the_bare_literal(self):
        # The failure mode this whole change exists to prevent: a new check
        # written as `role == "instructor"` silently excludes course
        # superusers again.
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for path in root.rglob("*.py"):
            text = str(path)
            if ".venv" in text or "/migrations/" in text:
                continue
            if path.name.startswith("test_") or path.name == "tests.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"role\s*[=!]=\s*['\"]instructor['\"]", line):
                    offenders.append(f"{path.relative_to(root)}:{number}")
        self.assertEqual(
            offenders,
            [],
            "these compare a role against 'instructor' directly, which excludes "
            "course superusers; use is_instructor() or INSTRUCTOR_ROLES",
        )
