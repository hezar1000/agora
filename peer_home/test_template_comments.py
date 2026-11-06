"""Template comments must not end up on the page.

`{# ... #}` is single-line only. Spanning one across lines does not comment
anything out: Django renders it verbatim, so the note appears in the interface.
It compiles, no test fails, and the text simply shows up on every page that
uses the template -- which for a base template is all of them.
"""

import pathlib
import re

from django.test import SimpleTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TemplateCommentTests(SimpleTestCase):
    def test_no_comment_spans_more_than_one_line(self):
        offenders = []
        for path in sorted(ROOT.rglob("*.html")):
            if ".venv" in str(path):
                continue
            text = path.read_text(errors="replace")
            for match in re.finditer(r"\{#(.*?)#\}", text, re.S):
                if "\n" in match.group(1):
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(ROOT)}:{line}")
        self.assertEqual(
            offenders,
            [],
            "{# #} is single-line only; these render as visible text. "
            "Use {% comment %}...{% endcomment %} instead.",
        )
