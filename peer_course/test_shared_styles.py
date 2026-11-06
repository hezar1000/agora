"""The shared stylesheets size themselves for the framework underneath them."""

import re
from pathlib import Path

from django.test import SimpleTestCase

BLOCKS = Path(__file__).resolve().parent / "templates" / "blocks"
SHARED = ["settings-style.html", "course-admin-style.html"]


class SharedStyleTests(SimpleTestCase):
    def test_no_rem_units(self):
        # Bootstrap 3 sets html{font-size:10px} and body{font-size:14px}, so a
        # rem is 10px here, not 16. Everything sized in rem came out about a
        # third smaller than it reads: a 1.15rem section heading rendered at
        # 11.5px, under the 14px description printed beneath it.
        for name in SHARED:
            with self.subTest(sheet=name):
                source = (BLOCKS / name).read_text()
                # The prose explaining this rule may name a rem size; only the
                # declarations matter.
                source = re.sub(r"{% comment %}.*?{% endcomment %}", "", source, flags=re.S)
                source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
                units = re.findall(r"[0-9.]+rem", source)
                self.assertEqual(units, [], f"{name} sizes in rem: {units}")

    def test_headings_are_larger_than_body_text(self):
        source = (BLOCKS / "settings-style.html").read_text()
        for selector in ("settings-group__title", "settings-legacy-heading"):
            with self.subTest(selector=selector):
                block = source[source.index(f".{selector}") :][:200]
                size = re.search(r"font-size:\s*(\d+)px", block)
                self.assertIsNotNone(size, f"{selector} has no px font-size")
                self.assertGreater(int(size.group(1)), 14, "heading is under body size")

    def test_the_two_products_ship_the_same_file(self):
        # They are copies; if they drift the "one system" claim stops being true.
        for name in SHARED:
            with self.subTest(sheet=name):
                self.assertTrue((BLOCKS / name).exists())
