"""The code a class has to be able to type.

`secrets.token_urlsafe(18)` gives "xK3-mQ9pR2sT7vW1yZ4aB6cD". It is
unguessable and unusable: read it out to a lecture hall and nobody gets in.
New courses get a student code made of three words instead. The TA and
instructor codes stay random, because `CourseBase.enroll` matches the
instructor code first, on any course, and hands out the instructor role.
"""

import re

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from peer_course import passphrase
from peer_course.base import CourseBase
from peer_course.forms import MIN_STUDENT_CODE_LENGTH
from peer_course.models import Course


class WordListTests(SimpleTestCase):
    def test_the_words_are_all_distinct(self):
        # A duplicate silently costs entropy and reads as a typo in the list.
        self.assertEqual(len(passphrase.WORDS), len(set(passphrase.WORDS)))

    def test_the_words_are_plain_lowercase_letters(self):
        # Anything else is a code somebody mistypes off a slide.
        odd = [w for w in passphrase.WORDS if not w.isalpha() or w != w.lower()]
        self.assertEqual(odd, [])

    def test_the_list_is_large_enough_to_be_worth_guessing_against(self):
        # 600 words over three positions is ~27 bits, against ten enrolment
        # attempts per account every five minutes.
        self.assertGreaterEqual(len(passphrase.WORDS), 500)

    def test_a_code_is_three_words_joined_by_hyphens(self):
        for _ in range(200):
            code = passphrase.generate()
            self.assertRegex(code, r"^[a-z]+-[a-z]+-[a-z]+$")
            self.assertTrue(all(w in passphrase.WORDS for w in code.split("-")))

    def test_a_code_never_repeats_a_word(self):
        for _ in range(500):
            words = passphrase.generate().split("-")
            self.assertEqual(len(words), len(set(words)))

    def test_every_code_clears_the_length_the_form_asks_for(self):
        # If the shortest possible code were rejected by clean_stucode, the
        # generator and the validator would disagree with each other.
        shortest = min(len(w) for w in passphrase.WORDS) * 3 + 2
        self.assertGreaterEqual(shortest, MIN_STUDENT_CODE_LENGTH)

    def test_two_codes_in_a_row_differ(self):
        self.assertNotEqual(passphrase.generate(), passphrase.generate())


class NewCourseCodeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("maker")

    def test_a_new_course_gets_a_student_code_of_words(self):
        course = CourseBase.create(self.user, "CPSC 999 2026W1", True)

        self.assertRegex(course.stucode, r"^[a-z]+-[a-z]+-[a-z]+$")

    def test_the_staff_codes_stay_random(self):
        # Typing the instructor code makes you an instructor of the course,
        # on any course, browsable or not.
        course = CourseBase.create(self.user, "CPSC 998 2026W1", True)

        for code in (course.tascode, course.instructor_code):
            with self.subTest(code=code):
                self.assertGreaterEqual(len(code), 20)
                self.assertNotRegex(code, r"^[a-z]+-[a-z]+-[a-z]+$")

    def test_the_three_codes_are_different_from_each_other(self):
        course = CourseBase.create(self.user, "CPSC 997 2026W1", True)

        self.assertEqual(
            len({course.stucode, course.tascode, course.instructor_code}), 3
        )

    def test_a_student_code_already_taken_is_not_handed_out_twice(self):
        taken = passphrase.generate()
        CourseBase.create(self.user, "CPSC 996 2026W1", True)
        Course.objects.filter(displayname="CPSC 996 2026W1").update(stucode=taken)

        calls = []

        def once():
            calls.append(1)
            return taken if len(calls) == 1 else passphrase.generate()

        code = CourseBase.generate_unique_code(make=once)

        self.assertNotEqual(code, taken)
        self.assertGreater(len(calls), 1)

    def test_a_word_code_still_enrolls_a_student(self):
        # The whole point: the code works when somebody types it.
        course = CourseBase.create(self.user, "CPSC 995 2026W1", True)
        student = User.objects.create_user("types-it-in")

        member = CourseBase.enroll(student, course.stucode)

        self.assertIsNotNone(member)
        self.assertEqual(member.role, "student")


class SettingsFormTests(TestCase):
    """What the settings page accepts when an instructor types a code in."""

    def setUp(self):
        self.course = CourseBase.create(
            User.objects.create_user("owner"), "CPSC 994 2026W1", True
        )

    def _bound(self, **overrides):
        """The form as the settings page posts it, with a field changed.

        Built from the unbound form's own initial values so that changing one
        code does not trip validation on twenty unrelated fields.
        """
        from django import forms as django_forms
        from peer_course.forms import CourseForm

        unbound = CourseForm(instance=self.course)
        data = {}
        for name, field in unbound.fields.items():
            value = unbound[name].value()
            if isinstance(field.widget, django_forms.CheckboxInput):
                if value:
                    data[name] = "on"
            elif value not in (None, ""):
                data[name] = value
        data.update(overrides)
        return CourseForm(data=data, instance=self.course)

    def test_the_form_it_is_given_is_valid_to_begin_with(self):
        # Otherwise the assertions below would prove nothing.
        form = self._bound()
        self.assertEqual(form.errors.as_data(), {}, form.errors.as_text())

    def test_a_three_word_student_code_is_accepted(self):
        form = self._bound(stucode=passphrase.generate())

        self.assertNotIn("stucode", form.errors)

    def test_a_one_word_student_code_is_still_refused(self):
        form = self._bound(stucode="lantern")

        self.assertIn("stucode", form.errors)

    def test_the_refusal_shows_what_a_good_code_looks_like(self):
        # The old message said "use a randomly generated code", which is
        # exactly the thing an instructor was trying to get away from.
        form = self._bound(stucode="x")

        message = " ".join(form.errors["stucode"])
        self.assertIn("words", message)
        self.assertRegex(message, r"[a-z]+-[a-z]+-[a-z]+")

    @staticmethod
    def _short_word_code():
        """Three words, short enough to be under the staff floor.

        A generated code runs from 14 to 29 characters, so `generate()` alone
        would clear a 20-character rule about half the time. The staff rule
        is a length floor, not a test for wordiness.
        """
        return "-".join(sorted(passphrase.WORDS, key=len)[:3])

    def test_a_short_ta_code_is_refused(self):
        # Typing the TA code makes you a TA; it is pasted, never read out.
        form = self._bound(tascode=self._short_word_code())

        self.assertIn("tascode", form.errors)

    def test_a_short_instructor_code_is_refused(self):
        form = self._bound(instructor_code=self._short_word_code())

        self.assertIn("instructor_code", form.errors)

    def test_the_staff_refusal_says_only_the_student_code_can_be_words(self):
        form = self._bound(tascode=self._short_word_code())

        self.assertIn("Only the student code can be words",
                      " ".join(form.errors["tascode"]))

    def test_the_same_short_code_is_fine_for_students(self):
        # The two rules differ on purpose, and this is the difference.
        form = self._bound(stucode=self._short_word_code())

        self.assertNotIn("stucode", form.errors)
