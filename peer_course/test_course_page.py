"""The parts of a course page that both products have, they present alike."""

from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parent / "templates"
PAGE = TEMPLATES / "course-home.html"


class SharedCourseUiTests(SimpleTestCase):
    def setUp(self):
        self.source = PAGE.read_text()

    def test_the_admin_area_uses_the_shared_component(self):
        # The same file exists on MTA, so the two course pages are one
        # component rather than two that merely resemble each other.
        self.assertIn("blocks/course-admin-style.html", self.source)
        self.assertIn("course-admin__card", self.source)
        self.assertTrue((TEMPLATES / "blocks" / "course-admin-style.html").exists())

    def test_it_does_not_use_classes_from_other_frameworks(self):
        # This page is Bootstrap 3. `ui container` is Semantic UI and `ml-2` is
        # Bootstrap 4, so both were inert: they looked like layout but did
        # nothing at all.
        self.assertNotIn('class="ui container"', self.source)
        self.assertNotIn("ml-2", self.source)
