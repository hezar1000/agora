"""Linking a CWL to an account, and the two ways it can be pointless.

Reconstructed from a real mistake: link_saml_identity was run for an
instructor who already had a second account federated with that same CWL.
The command accepted it, because it only checked Profile.cwl. The result was
inert -- _resolve_saml_user checks FederatedIdentity first and returns, so
the holder kept landing in the other account -- while this account lost its
password and became unreachable.
"""

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from peer_account.models import FederatedIdentity, Profile

ISSUER = "https://authentication.ubc.ca"


def run(*args):
    out = StringIO()
    call_command("link_saml_identity", *args, stdout=out, stderr=out)
    return out.getvalue()


class RefusalsTests(TestCase):
    def setUp(self):
        self.target = User.objects.create_user("instra", password="legacy!23")

    def test_it_refuses_a_cwl_another_account_is_federated_with(self):
        live = User.objects.create_user("87654321")
        live.set_unusable_password()
        live.save(update_fields=["password"])
        FederatedIdentity.objects.create(user=live, issuer=ISSUER, subject="instra")

        with self.assertRaises(CommandError) as caught:
            run("--username", "instra", "--cwl", "instra")

        self.assertIn("already federated", str(caught.exception))
        self.assertIn("87654321", str(caught.exception))

    def test_and_leaves_the_password_alone_when_it_refuses(self):
        # The damage was not the recorded CWL, which is inert. It was the
        # retired password on an account that then had no way in.
        live = User.objects.create_user("87654321")
        FederatedIdentity.objects.create(user=live, issuer=ISSUER, subject="instra")

        with self.assertRaises(CommandError):
            run("--username", "instra", "--cwl", "instra")

        self.target.refresh_from_db()
        self.assertTrue(self.target.has_usable_password())
        self.assertIsNone(Profile.objects.get(user=self.target).cwl)

    def test_it_still_refuses_a_cwl_recorded_on_another_profile(self):
        other = User.objects.create_user("someone-else")
        Profile.objects.filter(user=other).update(cwl="instra")

        with self.assertRaises(CommandError) as caught:
            run("--username", "instra", "--cwl", "instra")

        self.assertIn("already recorded", str(caught.exception))


class TheHappyPathStillWorksTests(TestCase):
    def test_an_unclaimed_cwl_links_and_retires_the_password(self):
        user = User.objects.create_user("instre", password="legacy!23")

        run("--username", "instre", "--cwl", "instre")

        user.refresh_from_db()
        self.assertEqual(Profile.objects.get(user=user).cwl, "instre")
        self.assertFalse(user.has_usable_password())

    def test_keep_password_stages_the_link_without_retiring_it(self):
        user = User.objects.create_user("later", password="legacy!23")

        run("--username", "later", "--cwl", "latercwl", "--keep-password")

        user.refresh_from_db()
        self.assertEqual(Profile.objects.get(user=user).cwl, "latercwl")
        self.assertTrue(user.has_usable_password())


class ClearingTests(TestCase):
    def test_clear_removes_a_recorded_cwl(self):
        user = User.objects.create_user("instra")
        Profile.objects.filter(user=user).update(cwl="instra")

        output = run("--username", "instra", "--clear")

        self.assertIsNone(Profile.objects.get(user=user).cwl)
        self.assertIn("Cleared CWL", output)

    def test_clear_says_so_when_there_is_nothing_to_clear(self):
        User.objects.create_user("clean")

        self.assertIn("nothing to do", run("--username", "clean", "--clear"))

    def test_clear_does_not_remove_a_real_binding(self):
        # A FederatedIdentity is a binding made at an actual sign-in.
        # Removing one is a separate decision, not a side effect of tidying
        # up a Profile field.
        user = User.objects.create_user("bound")
        FederatedIdentity.objects.create(user=user, issuer=ISSUER, subject="bound")
        Profile.objects.filter(user=user).update(cwl="bound")

        run("--username", "bound", "--clear")

        self.assertTrue(FederatedIdentity.objects.filter(user=user).exists())

    def test_cwl_and_clear_together_are_refused(self):
        User.objects.create_user("confused")

        with self.assertRaises(CommandError):
            run("--username", "confused", "--cwl", "x", "--clear")


class DryRunTests(TestCase):
    def test_a_dry_run_writes_nothing(self):
        # An earlier incident was a paste that took effect immediately. A
        # dry run is the difference between reading the consequence and
        # discovering it.
        user = User.objects.create_user("instrb-2", password="legacy!23")

        output = run("--username", "instrb-2", "--cwl", "instrb", "--dry-run")

        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())
        self.assertIsNone(Profile.objects.get(user=user).cwl)
        self.assertIn("would link", output)


class BatchTests(TestCase):
    def setUp(self):
        for name in ("instrb-2", "instrc", "instrd"):
            User.objects.create_user(name, password="legacy!23")

    def write(self, text):
        import tempfile

        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        handle.write(text)
        handle.close()
        return handle.name

    def test_a_good_batch_links_everyone(self):
        path = self.write(
            "# instructor CWLs, verified with the department\n"
            "instrb-2 instrb\n"
            "instrc instrc\n"
            "\n"
            "instrd instrd   # four courses on this one\n"
        )

        run("--batch", path)

        for name, cwl in (("instrb-2", "instrb"), ("instrc", "instrc"), ("instrd", "instrd")):
            user = User.objects.get(username=name)
            self.assertEqual(Profile.objects.get(user=user).cwl, cwl)
            self.assertFalse(user.has_usable_password())

    def test_one_bad_row_stops_the_whole_batch(self):
        # Half-linked instructors would be worse than none: some see their
        # courses, some get a new empty account, and nobody knows which.
        path = self.write("instrb-2 instrb\nnosuchaccount whoever\ninstrc instrc\n")

        with self.assertRaises(CommandError):
            run("--batch", path)

        self.assertIsNone(
            Profile.objects.get(user=User.objects.get(username="instrb-2")).cwl
        )
        self.assertTrue(
            User.objects.get(username="instrb-2").has_usable_password()
        )

    def test_it_refuses_a_cwl_already_federated_elsewhere(self):
        live = User.objects.create_user("87654321")
        FederatedIdentity.objects.create(user=live, issuer=ISSUER, subject="instra")
        User.objects.create_user("instra", password="legacy!23")
        path = self.write("instra instra\n")

        with self.assertRaises(CommandError):
            run("--batch", path)

        self.assertTrue(User.objects.get(username="instra").has_usable_password())

    def test_it_refuses_the_same_cwl_twice(self):
        path = self.write("instrb-2 shared\ninstrc shared\n")

        with self.assertRaises(CommandError) as caught:
            run("--batch", path)

        self.assertIn("given twice", str(caught.exception))

    def test_a_malformed_line_is_named_by_number(self):
        path = self.write("instrb-2 instrb\nthis line has three words here\n")

        with self.assertRaises(CommandError) as caught:
            run("--batch", path)

        self.assertIn(":2:", str(caught.exception))

    def test_a_batch_dry_run_writes_nothing(self):
        path = self.write("instrb-2 instrb\ninstrc instrc\n")

        output = run("--batch", path, "--dry-run")

        self.assertIn("Nothing written", output)
        self.assertTrue(User.objects.get(username="instrc").has_usable_password())
