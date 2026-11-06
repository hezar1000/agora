"""Moving a legacy account onto CWL rather than locking it out.

While a legacy account holds a password, _resolve_saml_user refuses to bind
a CWL to it, so the person cannot sign in with CWL at all. Clearing the
password is what lets the next CWL sign-in adopt the same account.
"""

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from django_otp.plugins.otp_totp.models import TOTPDevice

from peer_account.models import FederatedIdentity, Profile

ISSUER = "https://authentication.ubc.ca"


def run(*args):
    out = StringIO()
    call_command("retire_password", *args, stdout=out, stderr=out)
    return out.getvalue()


def resolve(**kwargs):
    from mta2.views import _resolve_saml_user

    result = _resolve_saml_user(**kwargs)
    return result[0] if isinstance(result, tuple) else result


class WhoMovesTests(TestCase):
    def setUp(self):
        self.ready = User.objects.create_user("instre", password="legacy!23")
        Profile.objects.filter(user=self.ready).update(cwl="instre")

        self.no_cwl = User.objects.create_user("mystery", password="legacy!23")

        self.enrolled = User.objects.create_user("careful", password="legacy!23")
        Profile.objects.filter(user=self.enrolled).update(cwl="careful")
        TOTPDevice.objects.create(
            user=self.enrolled, name="authenticator", confirmed=True
        )

        self.boss = User.objects.create_superuser("root", "r@example.com", "legacy!23")
        Profile.objects.filter(user=self.boss).update(cwl="rootcwl")

    def usable(self, user):
        user.refresh_from_db()
        return user.has_usable_password()

    def test_nothing_changes_without_apply(self):
        output = run()

        self.assertIn("Nothing changed", output)
        self.assertTrue(self.usable(self.ready))

    def test_an_account_with_a_cwl_has_its_password_retired(self):
        run("--apply")

        self.assertFalse(self.usable(self.ready))

    def test_an_account_with_no_cwl_is_left_alone(self):
        # Clearing this one's password would strand it: the resolver has
        # nothing to match on and would make a new account instead.
        output = run("--apply")

        self.assertTrue(self.usable(self.no_cwl))
        self.assertIn("no CWL on file", output)

    def test_an_account_that_enrolled_a_factor_is_left_alone(self):
        output = run("--apply")

        self.assertTrue(self.usable(self.enrolled))
        self.assertNotIn("careful", output)

    def test_a_superuser_is_not_touched_by_default(self):
        run("--apply")

        self.assertTrue(self.usable(self.boss))

    def test_a_superuser_can_be_included_on_purpose(self):
        run("--apply", "--include-superusers")

        self.assertFalse(self.usable(self.boss))

    def test_keep_spares_an_account(self):
        run("--apply", "--keep", "instre")

        self.assertTrue(self.usable(self.ready))


class TheWholeJourneyTests(TestCase):
    def test_after_retiring_the_password_cwl_adopts_the_same_account(self):
        user = User.objects.create_user("ginstructor", password="legacy!23")
        Profile.objects.filter(user=user).update(cwl="instrg")

        # Before: the resolver refuses outright.
        with self.assertRaises(PermissionError):
            resolve(issuer=ISSUER, student_number=None, cwl="instrg")

        run("--apply")

        # After: the same row, not a new one.
        resolved = resolve(issuer=ISSUER, student_number=None, cwl="instrg")
        self.assertEqual(resolved.pk, user.pk)

    def test_the_cwl_survives_the_command_writing_to_the_user(self):
        # The command calls user.save(update_fields=["password"]). Before the
        # save_user_profile receiver was removed, that wrote a stale cached
        # profile back and wiped the very cwl this depends on.
        user = User.objects.create_user("fragile", password="legacy!23")
        Profile.objects.filter(user=user).update(cwl="fragilecwl")

        run("--apply")

        self.assertEqual(Profile.objects.get(user=user).cwl, "fragilecwl")

    def test_a_cwl_already_bound_elsewhere_is_reported_not_merged(self):
        # The person has already been handed a second account by signing in
        # with CWL. Clearing this password does not merge them, and doing it
        # silently would leave two accounts nobody knows about.
        old = User.objects.create_user("old-account", password="legacy!23")
        Profile.objects.filter(user=old).update(cwl="dupe")
        newer = User.objects.create_user("dupe")
        newer.set_unusable_password()
        newer.save(update_fields=["password"])
        FederatedIdentity.objects.create(user=newer, issuer=ISSUER, subject="dupe")

        output = run("--apply")

        old.refresh_from_db()
        self.assertTrue(old.has_usable_password())
        self.assertIn("merge by hand", output)
        self.assertIn("already dupe", output)
