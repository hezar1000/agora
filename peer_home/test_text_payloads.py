"""Strings shown as text must not contain markup.

The lecture screen's hand-up counts were built in Python with a "</br>" in
them and assigned with textContent, so every instructor read
"Yellow: 0</br>Spoken:" during class. </br> is not a tag in any case: a br is
void and has no end tag.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [
    p
    for pattern in ("peer_*/**/*.py", "peer_*/**/*.html")
    for p in ROOT.glob(pattern)
    if ".venv" not in str(p)
]


class TextPayloadTests(SimpleTestCase):
    @staticmethod
    def _without_comments(source, suffix):
        # The prose explaining this rule quotes the tag; only code counts.
        source = re.sub(r"{% comment %}.*?{% endcomment %}", "", source, flags=re.S)
        source = re.sub(r"<!--.*?-->", "", source, flags=re.S)
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        if suffix == ".py":
            source = "\n".join(
                line.split("#", 1)[0] if line.lstrip().startswith("#") else line
                for line in source.splitlines()
            )
        return source

    def test_nothing_writes_a_closing_br_tag(self):
        offenders = []
        for path in SOURCES:
            if path.name == Path(__file__).name:
                continue
            code = self._without_comments(path.read_text(), path.suffix)
            for number, line in enumerate(code.splitlines(), 1):
                if "</br>" in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(offenders, [], "closing </br> tags: " + ", ".join(offenders))

    def test_the_counts_carry_no_markup(self):
        # They are assigned with textContent in agora.html.
        views = (ROOT / "peer_home" / "views.py").read_text()
        block = views[views.index("render_dict['count'] ="):][:900]
        self.assertNotIn("<", block, "markup in a string shown as text")
        self.assertNotIn("&nbsp;", block, "an entity in a string shown as text")
