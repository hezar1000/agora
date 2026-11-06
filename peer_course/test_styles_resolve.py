"""Every class this project styles itself is actually styled somewhere.

Renaming a class, or deleting a page's style block while its markup still uses
the old names, breaks nothing: the page loads, the rules simply stop applying.
That is how the settings link ended up white-on-white, and how this rework
first left `course-home__lead` and six bare lists with no rules at all.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = list(ROOT.glob("peer_*/templates/**/*.html"))

# Classes this codebase invents, as opposed to Bootstrap's or a library's.
OURS = re.compile(r"\b((?:course-admin|course-home|settings)(?:__|-)[a-z-]+)")


class StylesResolveTests(SimpleTestCase):
    def test_every_project_class_used_has_a_rule(self):
        # Definitions (".foo {") and uses (class="foo bar"), separately.
        defined = set()
        used = {}
        for path in TEMPLATES:
            source = path.read_text()
            for rule in re.finditer(r"\.([a-z][a-z0-9_-]*)\s*(?:,|\{|:)", source):
                defined.add(rule.group(1))
            for attr in re.finditer(r'class="([^"]*)"', source):
                for token in attr.group(1).split():
                    if OURS.fullmatch(token):
                        used.setdefault(token, set()).add(path.name)

        self.assertTrue(used, "no project classes found; the pattern is wrong")
        missing = {name: sorted(files) for name, files in used.items() if name not in defined}
        self.assertEqual(missing, {}, f"classes used with no rule anywhere: {missing}")
