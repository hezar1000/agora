"""The lecture screen must not block itself."""

import re
from pathlib import Path

from django.test import SimpleTestCase

SCREEN = Path(__file__).resolve().parent / "templates" / "agora.html"


class NonBlockingMessagesTests(SimpleTestCase):
    """`alert()` freezes the page until dismissed, which mid-lecture also
    stalls the WebSocket handlers keeping the queue current."""

    def setUp(self):
        self.source = SCREEN.read_text()

    def test_the_lecture_screen_never_calls_alert(self):
        self.assertNotIn("alert(", self.source)

    def test_messages_go_to_a_live_region(self):
        self.assertIn('id="agora-notice"', self.source)
        self.assertIn('role="status"', self.source)
        self.assertIn('aria-live="polite"', self.source)

    def test_the_region_starts_hidden(self):
        region = re.search(r'<div id="agora-notice".*?>', self.source, re.S)
        self.assertIsNotNone(region)
        self.assertIn("hidden", region.group(0))

    def test_notify_takes_one_argument_so_every_call_site_still_works(self):
        # The replacement was mechanical; a two-argument signature would have
        # broken every one of the eighteen call sites silently.
        self.assertIn("window.notify = function (message, kind)", self.source)
        for call in re.findall(r"notify\((.*?)\);", self.source):
            with self.subTest(call=call[:50]):
                self.assertTrue(call.strip(), "notify() called with nothing to say")

    def test_severity_is_not_carried_by_colour_alone(self):
        # A red panel says nothing to someone who cannot see red.
        self.assertIn(".agora-notice::before", self.source)
        self.assertIn('.agora-notice[data-kind="error"]::before', self.source)

    def test_confirm_is_left_alone(self):
        # It asks a question and needs an answer; a passive region cannot
        # collect one, so replacing it would lose the confirmation entirely.
        self.assertIn("confirm(", self.source)
