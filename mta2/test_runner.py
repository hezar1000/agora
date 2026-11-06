"""Test runner that leaves the second factor off unless a test asks for it.

Most of the suite creates users with passwords because that is the shortest
way to get a signed-in client, not because it is testing authentication. With
enrolment enforced, every one of those sessions is redirected to the
enrolment page and roughly thirty tests fail on a 302 they do not care about.

The setting is switched off here rather than in settings.py so that the
shipped default stays on -- production must enforce, and
``peer_account.test_mfa.ShippedDefaultTests`` fails if that ever changes.
Tests that are about the second factor turn it back on with
``@override_settings(MFA_ENFORCED=True)``.
"""

from django.conf import settings
from django.test.runner import DiscoverRunner


class MTATestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.MFA_ENFORCED = False
