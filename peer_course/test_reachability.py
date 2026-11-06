"""Every page a view renders exists, and the ones that matter have a route in.

Agora's migration deleted its course page along with the view that rendered it,
so nothing was left dangling to notice -- the roster, its exports and a
student's own participation simply had no link any more. A template-existence
check would not have caught that; asking whether a feature can be reached does.
"""

import pathlib
import re

from django.test import SimpleTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _templates():
    names = set()
    for path in ROOT.rglob("*.html"):
        if ".venv" in str(path):
            continue
        names.add(path.name)
        if "templates" in path.parts:
            names.add("/".join(path.parts[path.parts.index("templates") + 1:]))
    return names


def _sources():
    for path in ROOT.rglob("*.py"):
        if ".venv" in str(path) or "/migrations/" in str(path):
            continue
        yield path, path.read_text(errors="replace")


class RenderedTemplatesExistTests(SimpleTestCase):
    def test_every_rendered_template_is_present(self):
        available = _templates()
        missing = []
        for path, text in _sources():
            for match in re.finditer(
                r"""(?:render|get_template|render_to_string)\s*\(\s*"""
                r"""(?:request\s*,\s*)?["']([\w\-./]+\.html)["']""",
                text,
            ):
                name = match.group(1)
                if name in available:
                    continue
                line = text[: match.start()].count("\n") + 1
                # A route that is commented out cannot 500; report only live ones.
                missing.append(f"{path.relative_to(ROOT)}:{line} -> {name}")
        self.assertEqual(
            missing, [], "these views render a template that does not exist"
        )
