"""The course settings page groups its fields instead of listing them raw."""

import re
from pathlib import Path

from django import forms
from django.test import SimpleTestCase

from .forms import CourseForm

PAGE = Path(__file__).resolve().parent / "templates" / "course-edit.html"


class SettingsFieldsetTests(SimpleTestCase):
    def setUp(self):
        self.form = CourseForm()

    def test_every_field_is_either_grouped_or_hand_built(self):
        # This is the bug this test exists for: `manually_started`,
        # `show_participation_grade` and `allow_multiple_queues` were listed in
        # Meta.fields but rendered nowhere. An unrendered checkbox is absent
        # from the POST, and an absent checkbox cleans to False -- so every
        # Save silently switched all three off.
        grouped = [name for _, _, _, names in CourseForm.FIELDSETS for name in names]
        accounted = sorted(grouped + list(CourseForm.CUSTOM_FIELDS))
        self.assertEqual(
            accounted,
            sorted(self.form.fields),
            "a field is in the form but in no group and not hand-built",
        )
        self.assertEqual(len(accounted), len(set(accounted)), "a field is claimed twice")

    def test_every_field_actually_appears_on_the_page(self):
        source = PAGE.read_text()
        rendered = set(re.findall(r"formCourse\.([a-z_]+)", source))
        grouped = {n for _, _, _, names in CourseForm.FIELDSETS for n in names}
        for name in self.form.fields:
            with self.subTest(field=name):
                self.assertTrue(
                    name in grouped or name in rendered,
                    f"{name} is saved by this form but never shown, so saving clears it",
                )

    def test_the_enrolment_codes_are_kept_together(self):
        codes = next(
            names for slug, _, _, names in CourseForm.FIELDSETS if slug == "enrolment"
        )
        self.assertEqual(set(codes), {"instructor_code", "tascode", "stucode"})

    def test_the_first_groups_match_the_other_product(self):
        # MTA has the same three, in the same order.
        opening = [slug for slug, _, _, _ in CourseForm.FIELDSETS][:3]
        self.assertEqual(opening, ["course", "visibility", "enrolment"])

    def test_checkboxes_are_not_given_the_form_control_class(self):
        for name, field in self.form.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                with self.subTest(field=name):
                    self.assertNotIn("form-control", field.widget.attrs.get("class", ""))

    def test_validation_errors_are_shown_on_the_page(self):
        # They used to reach the instructor only as an alert() telling them to
        # open the browser console.
        source = PAGE.read_text()
        self.assertNotIn("check your console for details", source)
        self.assertIn("Nothing was saved.", source)

    def test_no_group_id_collides_with_a_hand_written_one(self):
        # The nav links to these by anchor. A group sharing an id with a
        # heading further down the page produces two elements with one id,
        # and the later link silently jumps to the wrong section.
        source = PAGE.read_text()
        hand_written = set(re.findall(r'<h[1-6][^>]*\bid="([^"]+)"', source))
        for slug, _, _, _ in CourseForm.FIELDSETS:
            with self.subTest(group=slug):
                self.assertNotIn(slug, hand_written)
