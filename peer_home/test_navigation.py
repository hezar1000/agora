"""The navigation names three distinct destinations."""

import re
from pathlib import Path

from django.test import SimpleTestCase

NAV = Path(__file__).resolve().parent / "templates" / "base.html"


class NavigationTests(SimpleTestCase):
    def setUp(self):
        self.source = NAV.read_text()
        nav = re.search(
            r'<ul class="nav navbar-nav">(.*?)</ul>', self.source, re.S
        ).group(1)
        # Strip comments in both forms: they explain the old names, and would
        # otherwise trip the checks that those names are gone.
        nav = re.sub(r"\{#.*?#\}", "", nav, flags=re.S)
        self.nav = re.sub(
            r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "", nav, flags=re.S
        )

    def test_no_two_items_lead_to_the_same_place(self):
        # "Dashboard" and "Participation" both pointed at the lecture screen,
        # so two of the three items went to one page and the course's own page
        # was not among them.
        targets = re.findall(r'<a href="([^"]+)"', self.nav)
        self.assertEqual(
            len(targets), len(set(targets)), f"duplicate destinations: {targets}"
        )

    def test_the_lecture_screen_is_called_the_lecture(self):
        # It is the live queue, not a dashboard; calling it one is why nobody
        # could find the page that actually summarises a course.
        self.assertIn("Lecture", self.nav)
        self.assertNotIn("Dashboard", self.nav)

    def test_the_course_page_is_offered(self):
        self.assertIn("/course/{{ request.session.course_id }}/", self.nav)

    def test_administration_does_not_require_a_chosen_course(self):
        course_block = self.nav[
            self.nav.index("{% if 'course_id' in request.session %}") :
        ]
        admin_at = course_block.index("/admin/")
        block_end = course_block.index("{% endif %}\n\n")
        self.assertGreater(
            admin_at, block_end, "administration is hidden until a course is chosen"
        )

    def test_it_is_spelled_correctly(self):
        self.assertIn("Administration", self.nav)
        self.assertNotIn("Adminstration", self.source)
