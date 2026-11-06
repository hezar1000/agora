"""The demo command, exercised end to end so it cannot fail in production."""

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase

from peer_course import participation_standing as standing
from peer_course.models import Course, CourseMember, HandRaise


class DemoCommandTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(username="prof")

    def build(self, **kwargs):
        call_command(
            "make_participation_demo",
            instructor="prof",
            lectures=kwargs.get("lectures", 6),
            students=kwargs.get("students", 8),
            verbosity=0,
        )

    def test_it_builds_one_course_per_scheme(self):
        self.build()
        names = sorted(
            Course.objects.filter(displayname__startswith="DEMO ").values_list(
                "displayname", flat=True
            )
        )
        self.assertEqual(len(names), 2)

    def test_the_instructor_can_reach_both(self):
        self.build()
        self.assertEqual(
            CourseMember.objects.filter(
                user=self.instructor, role="instructor"
            ).count(),
            2,
        )

    def test_every_student_page_renders_a_standing(self):
        # The point of the exercise: each generated student must produce the
        # page an instructor is about to look at.
        self.build()
        checked = 0
        for course in Course.objects.filter(displayname__startswith="DEMO "):
            for member in course.members.filter(role="student"):
                result = standing.standing_for(course, member)
                self.assertIsNotNone(result)
                self.assertTrue(result["breakdown"])
                checked += 1
        self.assertGreater(checked, 10)

    def test_the_two_courses_show_different_pictures(self):
        self.build()
        by_name = {
            course.displayname: course
            for course in Course.objects.filter(displayname__startswith="DEMO ")
        }
        per_lecture = next(c for n, c in by_name.items() if "per-lecture" in n)
        term_wide = next(c for n, c in by_name.items() if "attendance" in n)

        a = standing.standing_for(
            per_lecture, per_lecture.members.filter(role="student").first()
        )
        b = standing.standing_for(
            term_wide, term_wide.members.filter(role="student").first()
        )

        self.assertTrue(a["lecture_grades"])
        self.assertEqual(a["curve"], [])
        self.assertTrue(b["curve"])
        self.assertEqual(b["lecture_grades"], [])

    def test_the_first_lecture_is_excluded(self):
        self.build()
        course = Course.objects.filter(displayname__startswith="DEMO ").first()
        first = course.sessions.filter(is_template=False).order_by(
            "session_date"
        ).first()
        self.assertFalse(first.counts_for_participation)

    def test_the_invented_accounts_cannot_be_signed_in_to(self):
        self.build()
        for user in User.objects.filter(username__startswith="demo-student-"):
            with self.subTest(user=user.username):
                self.assertFalse(user.is_active)
                self.assertFalse(user.has_usable_password())

    def test_removal_leaves_nothing_behind(self):
        self.build()
        call_command("make_participation_demo", remove=True, verbosity=0)

        self.assertFalse(
            Course.objects.filter(displayname__startswith="DEMO ").exists()
        )
        self.assertFalse(
            User.objects.filter(username__startswith="demo-student-").exists()
        )
        self.assertFalse(HandRaise.objects.exists())
        # The real account is untouched.
        self.assertTrue(User.objects.filter(username="prof").exists())

    def test_rebuilding_gives_the_same_data(self):
        self.build()
        before = HandRaise.objects.count()
        call_command("make_participation_demo", remove=True, verbosity=0)
        self.build()
        self.assertEqual(HandRaise.objects.count(), before)

    def test_an_unknown_instructor_is_refused(self):
        with self.assertRaises(CommandError):
            call_command(
                "make_participation_demo", instructor="nobody", verbosity=0
            )
