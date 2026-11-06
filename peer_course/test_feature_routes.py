"""Features an instructor or a student needs must be reachable by clicking.

The hardening commit removed Agora's course page and left several working
features with no link to them at all: the roster, the roster's exports, and --
once the participation charts were added -- a student's own standing. Nothing
was broken, so nothing failed; they were simply gone from the interface.
"""

import pathlib
import re

from django.test import SimpleTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: What has to be reachable, and a fragment that proves a link exists.
REACHABLE = {
    "the class roster": r"list_users/",
    "course settings": r"url\s+'course:edit'|/edit/",
    "a student's own participation": r"/course/\{\{\s*course\.id\s*\}\}/\{\{",
    "the lecture screen": r"peer_home:agora_dash",
    "the course page": r"/course/\{\{ request\.session\.course_id \}\}/",
}


class FeatureReachabilityTests(SimpleTestCase):
    def setUp(self):
        self.markup = "".join(
            path.read_text(errors="replace")
            for path in ROOT.rglob("*.html")
            if ".venv" not in str(path)
        )

    def test_each_feature_has_a_link_somewhere(self):
        unreachable = [
            label
            for label, pattern in REACHABLE.items()
            if not re.search(pattern, self.markup)
        ]
        self.assertEqual(
            unreachable,
            [],
            "these work but nothing links to them, so nobody can get to them",
        )
