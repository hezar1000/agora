"""Editing a participation scheme through the settings page."""

import inspect
import re
from pathlib import Path

from django.test import TestCase

from peer_course import participation_curves as curves
from peer_course import participation_forms, participation_presets
from peer_course.models import Course, MeasureRubric, ParticipationScheme
from peer_course.participation_forms import (
    MeasureRubricForm,
    ParticipationSchemeForm,
)
from peer_course.views import CourseViews


class WrittenCurveTests(TestCase):
    def test_a_curve_round_trips(self):
        text = "0 -> 0%, 30 -> 60%, median -> 70%"
        self.assertEqual(
            curves.format_anchors(curves.parse_anchors(text)), text
        )

    def test_an_empty_curve_is_no_curve(self):
        self.assertEqual(curves.parse_anchors("   "), [])

    def test_the_message_names_the_piece_that_is_wrong(self):
        with self.assertRaises(curves.CurveError) as caught:
            curves.parse_anchors("0 -> 0%, thirty -> 60%")
        self.assertIn("thirty", str(caught.exception))

    def test_two_rewards_at_one_position_are_refused(self):
        with self.assertRaises(curves.CurveError):
            curves.parse_anchors("30 -> 60%, 30 -> 80%")

    def test_the_same_reward_twice_is_harmless(self):
        self.assertEqual(len(curves.parse_anchors("30 -> 60%, 30 -> 60%")), 2)


class SchemeFormTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Scheme form", browsable=True, archived=False,
            agora_mode=True,
        )
        self.scheme = participation_presets.apply_preset(
            self.course, participation_presets.COVERAGE_AND_VOLUME
        )

    def test_an_untouched_preset_is_named_without_qualification(self):
        form = ParticipationSchemeForm(instance=self.scheme)
        label = dict(form.fields["preset"].choices)[participation_forms.KEEP]
        self.assertIn("Attendance and contributions", label)
        self.assertNotIn("edited", label)

    def test_an_edited_preset_says_so(self):
        # A preset is a starting point, not a mode: editing one keeps its name
        # and admits the edit, so the reset stays discoverable.
        self.scheme.dropped_lectures = 5
        self.scheme.save()

        form = ParticipationSchemeForm(instance=self.scheme)
        label = dict(form.fields["preset"].choices)[participation_forms.KEEP]
        self.assertIn("edited", label)

    def test_editing_a_curve_also_counts_as_edited(self):
        rubric = self.scheme.rubrics.get(measure=ParticipationScheme.VOLUME)
        rubric.absolute_anchors = [{"at": 0, "gives": 0.0}, {"at": 50, "gives": 0.6}]
        rubric.save()

        form = ParticipationSchemeForm(instance=self.scheme)
        label = dict(form.fields["preset"].choices)[participation_forms.KEEP]
        self.assertIn("edited", label)

    def test_dropping_every_lecture_is_refused(self):
        form = ParticipationSchemeForm(
            {
                "scheme-raise_value": "1",
                "scheme-speak_bonus": "0",
                "scheme-lectures_expected": "4",
                "scheme-dropped_lectures": "4",
            },
            instance=self.scheme,
            prefix="scheme",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("dropped_lectures", form.errors)


class RubricFormTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Rubric form", browsable=True, archived=False,
            agora_mode=True,
        )
        self.scheme = participation_presets.apply_preset(
            self.course, participation_presets.COVERAGE_AND_VOLUME
        )
        self.rubric = self.scheme.rubrics.get(measure=ParticipationScheme.VOLUME)
        # Fixed anchors of this test's own, so that correcting a preset's
        # numbers is not also an edit to the form tests.
        self.rubric.absolute_anchors = [{"at": 0, "gives": 0.0},
                                        {"at": 30, "gives": 0.6}]
        self.rubric.save(update_fields=["absolute_anchors"])

    def _data(self, **overrides):
        data = {
            "weight": "10",
            "absolute_interpolation": "value",
            "relative_interpolation": "rank",
            "combination": "better",
            "absolute_anchors_text": "0 -> 0%, 30 -> 60%",
            "relative_anchors_text": "median -> 70%, max -> 100%",
        }
        data.update(overrides)
        return data

    def test_the_stored_curve_is_shown_in_writing(self):
        form = MeasureRubricForm(instance=self.rubric)
        self.assertEqual(
            form.fields["absolute_anchors_text"].initial, "0 -> 0%, 30 -> 60%"
        )

    def test_editing_the_written_curve_stores_anchors(self):
        form = MeasureRubricForm(
            self._data(absolute_anchors_text="0 -> 0%, 45 -> 75%"),
            instance=self.rubric,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.rubric.refresh_from_db()
        self.assertEqual(
            self.rubric.absolute_anchors,
            [{"at": 0, "gives": 0.0}, {"at": 45, "gives": 0.75}],
        )

    def test_a_bad_curve_reports_on_its_own_field(self):
        form = MeasureRubricForm(
            self._data(relative_anchors_text="median -> lots"), instance=self.rubric
        )
        self.assertFalse(form.is_valid())
        self.assertIn("relative_anchors_text", form.errors)

    def test_a_weighted_measure_with_no_curve_is_refused(self):
        # It would score nothing for everybody, which reads as a grading bug
        # rather than a setting.
        form = MeasureRubricForm(
            self._data(absolute_anchors_text="", relative_anchors_text=""),
            instance=self.rubric,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("absolute_anchors_text", form.errors)

    def test_choosing_a_curve_that_is_empty_is_refused(self):
        form = MeasureRubricForm(
            self._data(combination="relative", relative_anchors_text=""),
            instance=self.rubric,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("relative_anchors_text", form.errors)

    def test_interpolation_is_choosable_per_curve(self):
        form = MeasureRubricForm(
            self._data(absolute_interpolation="rank", relative_interpolation="value"),
            instance=self.rubric,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.rubric.refresh_from_db()
        self.assertEqual(self.rubric.absolute_interpolation, "rank")
        self.assertEqual(self.rubric.relative_interpolation, "value")


class SchemeBootstrapTests(TestCase):
    def test_opening_the_page_seeds_a_scheme_matching_current_behaviour(self):
        course = Course.objects.create(
            displayname="Bootstrap", browsable=True, archived=False,
            agora_mode=True, max_hand_raise_points=1,
        )
        self.assertFalse(hasattr(course, "participation_scheme"))

        CourseViews._participation_forms(course)

        course.refresh_from_db()
        # A cap of one point per session is how attendance was expressed before
        # schemes existed, so that is what the course is seeded with.
        self.assertEqual(
            course.participation_scheme.preset, participation_presets.ATTENDANCE
        )

    def test_seeding_twice_does_not_duplicate(self):
        course = Course.objects.create(
            displayname="Twice", browsable=True, archived=False, agora_mode=True
        )
        CourseViews._participation_forms(course)
        course.refresh_from_db()
        CourseViews._participation_forms(course)

        self.assertEqual(ParticipationScheme.objects.filter(course=course).count(), 1)


class ViewWiringTests(TestCase):
    def test_the_edit_view_still_requires_a_login(self):
        # Inserting the helper above `edit` once carried its decorators away
        # with it, leaving the settings page reachable without signing in.
        source = inspect.getsource(CourseViews.edit)
        self.assertIn("login_required", inspect.getsource(CourseViews))
        wrapped = re.search(
            r"@staticmethod\s+@login_required\s+def edit\(",
            inspect.getsource(CourseViews),
        )
        self.assertIsNotNone(
            wrapped, "edit() lost @login_required; the settings page is open"
        )

    def test_the_edit_url_can_be_reversed(self):
        from django.urls import reverse

        # Applying a preset redirects back here; an unnamed pattern would have
        # raised NoReverseMatch on every apply.
        self.assertEqual(reverse("course:edit", kwargs={"cid": 3}), "/course/3/edit/")

    def test_the_settings_page_renders_the_scheme(self):
        source = (
            Path(__file__).resolve().parent / "templates" / "course-edit.html"
        ).read_text()
        self.assertIn('id="participation"', source)
        self.assertIn("scheme_form.preset", source)
        self.assertIn("rubric_formset.management_form", source)

    def test_every_scheme_field_reaches_the_page(self):
        """A field on the form but absent from the template is silently reset.

        The template picks scheme fields by name, so adding one to the form
        without adding it there leaves it out of every POST -- and a
        BooleanField missing from a POST cleans to False. The setting would
        clear itself each time anyone saved the page, with nothing on screen
        to say it had. That is how four Agora settings were being lost.
        """
        source = (
            Path(__file__).resolve().parent / "templates" / "course-edit.html"
        ).read_text()
        for name in ParticipationSchemeForm().fields:
            with self.subTest(field=name):
                self.assertTrue(
                    f'"{name}"' in source or f"scheme_form.{name}" in source,
                    f"scheme_form.{name} is never rendered by "
                    "course-edit.html, so saving the page would clear it",
                )

    def test_a_checkbox_is_not_stretched_into_a_slab(self):
        # Bootstrap 3 gives .form-control width 100%, which turns a checkbox
        # into a full-width grey box with the tick lost inside it.
        form = ParticipationSchemeForm()
        classes = form.fields["volume_excludes_covered"].widget.attrs.get(
            "class", ""
        )
        self.assertNotIn("form-control", classes)
