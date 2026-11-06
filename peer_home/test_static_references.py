"""Every static file a page asks for exists, and so does everything it pulls in.

Agora carried a few hundred static files from MTA that no page loaded. They
were deleted by finding what the templates reference; this keeps that list
honest in the other direction, so a template never points at a file that is
not shipped.
"""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
TEMPLATE_REFERENCE = re.compile(
    r"""\{%\s*static\s+['"]([^'"]+)['"]|['"(]/static/([^'"?#)\s]+)"""
)
CSS_URL = re.compile(r"""url\(\s*['"]?([^'")?#]+)""")


def _template_references():
    for template in ROOT.glob("*/templates/**/*.html"):
        for match in TEMPLATE_REFERENCE.finditer(template.read_text()):
            yield template, match.group(1) or match.group(2)


class StaticReferenceTests(SimpleTestCase):
    def test_every_static_file_a_template_names_exists(self):
        missing = [
            f"{template.relative_to(ROOT)}: {name}"
            for template, name in _template_references()
            if "{" not in name and not finders.find(name)
        ]
        self.assertEqual(missing, [])

    def test_every_file_a_referenced_stylesheet_loads_exists(self):
        missing = []
        for template, name in _template_references():
            if not name.endswith(".css"):
                continue
            path = finders.find(name)
            if not path:
                continue
            for target in CSS_URL.findall(Path(path).read_text(errors="ignore")):
                if target.startswith(("data:", "http:", "https:", "//")):
                    continue
                if not (Path(path).parent / target).exists():
                    missing.append(f"{name}: {target}")
        self.assertEqual(missing, [])
