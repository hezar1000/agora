"""List names come from `color_config`, the store that already held them."""

import re
from pathlib import Path

from django.test import TestCase

from peer_course.models import Course


class QueueLabelTests(TestCase):
    def course(self, config=None):
        return Course.objects.create(
            displayname="Named", browsable=True, archived=False,
            color_config=config or "",
        )

    def test_an_unconfigured_course_falls_back_to_the_colours(self):
        self.assertEqual(
            self.course().queue_labels(),
            {1: "Green", 2: "Blue", 3: "Red", 4: "Yellow"},
        )

    def test_configured_names_are_used(self):
        # A real setting from production: CPSC 320 and CPSC 100.
        labels = self.course(
            '{"green": "Participate", "blue": "Other", "red": "Red"}'
        ).queue_labels()
        self.assertEqual(labels[1], "Participate")
        self.assertEqual(labels[2], "Other")

    def test_an_ampersand_is_rejected_and_the_name_is_lost(self):
        """Documents current behaviour, which is very likely a defect.

        `get_color_config_dict` refuses any name containing < > or &, as an
        injection guard, and silently substitutes the colour. CPSC 320 has
        `"blue": "Q&A"` stored in production and has been shown "Blue" ever
        since. Escaping on output would be the right guard -- Django templates
        and `json_script` both already escape -- but loosening an injection
        check is not something to slip into an unrelated change.
        """
        self.assertEqual(self.course('{"blue": "Q&A"}').queue_labels()[2], "Blue")

    def test_a_name_over_fifteen_characters_is_also_dropped(self):
        self.assertEqual(
            self.course('{"green": "Questions about the reading"}').queue_labels()[1],
            "Green",
        )

    def test_a_partly_configured_course_keeps_the_rest(self):
        labels = self.course('{"green": "bestlist"}').queue_labels()
        self.assertEqual(labels[1], "bestlist")
        self.assertEqual(labels[3], "Red")

    def test_invalid_json_does_not_break_a_lecture(self):
        self.assertEqual(self.course("{not json").queue_labels()[1], "Green")

    def test_a_blank_name_falls_back_rather_than_showing_nothing(self):
        self.assertEqual(self.course('{"blue": "   "}').queue_labels()[2], "Blue")

    def test_every_position_is_present(self):
        self.assertEqual(sorted(self.course().queue_labels()), [1, 2, 3, 4])


class NoSecondStoreTests(TestCase):
    """One store for these names, not two that can disagree."""

    def test_there_is_no_parallel_queue_model(self):
        import peer_course.models as models

        self.assertFalse(
            hasattr(models, "CourseQueue"),
            "a second place to name a list can disagree with color_config, "
            "which is what the dashboard edits",
        )

    def test_the_settings_page_does_not_offer_a_second_rename(self):
        source = (
            Path(__file__).resolve().parent / "templates" / "course-edit.html"
        ).read_text()
        self.assertNotIn("What the lists are called", source)
        self.assertNotIn("queue_1_name", source)

    def test_the_lecture_screen_reads_the_configured_names(self):
        source = (
            Path(__file__).resolve().parent / "templates" / "agora.html"
        ).read_text()
        self.assertIn('json_script:"queue-labels"', source)
        for position in (1, 2, 3, 4):
            with self.subTest(position=position):
                self.assertIn(f"named('{position}',", source)
