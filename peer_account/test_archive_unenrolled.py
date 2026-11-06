"""Old password accounts that never enrolled cannot sign in at all.

Being offered enrolment is not enough for an account that predates the
second factor: whoever holds the old password could enrol an authenticator
of their own and become the account. So they are archived instead --
is_active=False, which both sign-in routes already refuse.

Archived, not deleted. CourseMember.user cascades, so deleting one of these
would take its hand raises and its grades with it.
"""

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from peer_course.base import CourseBase
from peer_course.models import CourseMember


def resolve(**kwargs):
    """_resolve_saml_user, whichever shape this product's copy returns.

    Agora's returns (user, created); MTA's returns the user. The branches
    have diverged and this file is otherwise byte-identical on both.
    """
    from mta2.views import _resolve_saml_user

    result = _resolve_saml_user(**kwargs)
    return result[0] if isinstance(result, tuple) else result


def run(*args):
    out = StringIO()
    call_command("archive_unenrolled", *args, stdout=out, stderr=out)
    return out.getvalue()


class WhoGetsArchivedTests(TestCase):
    def setUp(self):
        self.old = User.objects.create_user("old-password-account", password="x!23456")
        self.enrolled = User.objects.create_user("enrolled", password="x!23456")
        TOTPDevice.objects.create(
            user=self.enrolled, name="authenticator", confirmed=True
        )
        self.cwl = User.objects.create_user("cwl-person")
        self.cwl.set_unusable_password()
        self.cwl.save()
        self.boss = User.objects.create_superuser("root", "root@example.com", "x!23456")

    def _active(self, user):
        user.refresh_from_db()
        return user.is_active

    def test_it_changes_nothing_without_apply(self):
        output = run()

        self.assertIn("Nothing changed", output)
        self.assertTrue(self._active(self.old))

    def test_an_old_password_account_is_archived(self):
        run("--apply")

        self.assertFalse(self._active(self.old))

    def test_an_enrolled_account_is_left_alone(self):
        run("--apply")

        self.assertTrue(self._active(self.enrolled))

    def test_a_cwl_account_is_left_alone(self):
        # No password to steal, and UBC's sign-in carries its own factor.
        run("--apply")

        self.assertTrue(self._active(self.cwl))

    def test_a_superuser_is_not_archived_by_accident(self):
        output = run("--apply")

        self.assertTrue(self._active(self.boss))
        self.assertIn("superusers, NOT archived", output)

    def test_a_superuser_can_be_archived_on_purpose(self):
        run("--apply", "--include-superusers")

        self.assertFalse(self._active(self.boss))

    def test_an_account_can_be_kept_by_name(self):
        run("--apply", "--keep", "old-password-account")

        self.assertTrue(self._active(self.old))

    def test_running_it_twice_is_harmless(self):
        run("--apply")
        second = run("--apply")

        self.assertIn("nothing to archive", second)


class ArchivingKeepsTheRecordsTests(TestCase):
    def test_a_course_history_survives_being_archived(self):
        # The reason this is is_active=False and not delete(): CourseMember
        # cascades from User, and on Agora every hand raise and every
        # participation row cascades from CourseMember in turn. Deleting one
        # of these accounts would take a term of grades with it.
        owner = User.objects.create_user("course-owner")
        user = User.objects.create_user("has-history", password="x!23456")
        course = CourseBase.create(owner, "CPSC 999 2026W1", True)
        member = CourseMember.objects.create(
            course=course, user=user, role="student", active=True
        )

        run("--apply")

        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertTrue(CourseMember.objects.filter(pk=member.pk).exists())
        self.assertEqual(
            CourseMember.objects.get(pk=member.pk).course_id, course.id
        )


@override_settings(MFA_ENFORCED=True)
class AnArchivedAccountCannotSignInTests(TestCase):
    """Every route in, refused."""

    def setUp(self):
        self.user = User.objects.create_user("archived", password="x!23456")
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

    def test_the_password_form_refuses_it(self):
        client = Client()

        client.post(reverse("account:login"), {
            "stid": "archived", "password": "x!23456",
        })

        self.assertNotIn("_auth_user_id", client.session)

    def test_the_admin_form_refuses_it(self):
        client = Client()

        client.post("/admin/login/", {
            "username": "archived", "password": "x!23456", "next": "/admin/",
        })

        self.assertNotIn("_auth_user_id", client.session)

    def test_cwl_refuses_to_bind_it(self):
        # An archived account must not come back to life by signing in with
        # a CWL that happens to be recorded against it.
        from peer_account.models import Profile

        Profile.objects.filter(user=self.user).update(cwl="arch1")

        with self.assertRaises(PermissionError):
            resolve(
                issuer="https://authentication.ubc.ca",
                student_number=None,
                cwl="arch1",
            )

    def test_it_cannot_be_enrolled_back_in_by_whoever_has_the_password(self):
        client = Client()
        client.login(username="archived", password="x!23456")

        response = client.get(reverse("account:mfa_setup"))

        self.assertNotEqual(response.status_code, 200)


class GracePeriodTests(TestCase):
    """Somebody who signed up minutes ago has no device either.

    Without a grace period this command is safe to run once by hand and
    unsafe to run on a schedule, because it would sweep away every signup
    still in the middle of scanning the QR.
    """

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone

        self.fresh = User.objects.create_user("just-signed-up", password="x!23456")
        self.stale = User.objects.create_user("never-finished", password="x!23456")
        User.objects.filter(pk=self.stale.pk).update(
            date_joined=timezone.now() - timedelta(days=30)
        )

    def test_without_a_grace_period_both_go(self):
        run("--apply")

        self.fresh.refresh_from_db()
        self.stale.refresh_from_db()
        self.assertFalse(self.fresh.is_active)
        self.assertFalse(self.stale.is_active)

    def test_a_grace_period_spares_the_fresh_signup(self):
        output = run("--apply", "--min-age-days", "7")

        self.fresh.refresh_from_db()
        self.stale.refresh_from_db()
        self.assertTrue(self.fresh.is_active)
        self.assertFalse(self.stale.is_active)
        self.assertIn("too new to archive", output)


class OnlyPrefixTests(TestCase):
    """Sweeping the retired one-time-session accounts without the humans.

    650 of the 732 accounts found on Agora were temp_instructor_* and
    temp_student_* left behind by the one-time-session feature. The other 82
    are named accounts, several of them faculty who used the site this year.
    Those two populations need different decisions.
    """

    def setUp(self):
        self.temp = User.objects.create_user(
            "temp_student_0d1e2f3a", password="x!23456"
        )
        self.human = User.objects.create_user("PatSampleton", password="x!23456")

    def test_a_prefix_sweeps_only_that_population(self):
        run("--apply", "--only-prefix", "temp_student_")

        self.temp.refresh_from_db()
        self.human.refresh_from_db()
        self.assertFalse(self.temp.is_active)
        self.assertTrue(self.human.is_active)

    def test_prefixes_add_up(self):
        other = User.objects.create_user(
            "temp_instructor_0a1b2c3d", password="x!23456"
        )

        run("--apply", "--only-prefix", "temp_student_",
            "--only-prefix", "temp_instructor_")

        other.refresh_from_db()
        self.temp.refresh_from_db()
        self.human.refresh_from_db()
        self.assertFalse(other.is_active)
        self.assertFalse(self.temp.is_active)
        self.assertTrue(self.human.is_active)

    def test_the_report_says_whether_an_account_can_move_to_cwl(self):
        from peer_account.models import Profile

        Profile.objects.filter(user=self.human).update(cwl="nhutch")

        output = run()

        self.assertIn("CWL:nhutch", output)
        self.assertIn("no CWL on file", output)


class ProfileIsNotClobberedTests(TestCase):
    """A CWL binding must survive an unrelated user.save().

    save_user_profile used to write the cached profile back on every user
    save, dropping a cwl written in between by a queryset update -- which is
    how link_saml_identity and the SAML resolver write it. The next CWL
    sign-in would then create a new account and orphan this one along with
    its course memberships.
    """

    def test_an_unrelated_save_keeps_the_cwl(self):
        from peer_account.models import Profile

        user = User.objects.create_user("legacy-instructor")
        Profile.objects.filter(user=user).update(cwl="theircwl")

        user.first_name = "Changed"
        user.save(update_fields=["first_name"])

        self.assertEqual(Profile.objects.get(user=user).cwl, "theircwl")

    def test_the_cached_profile_does_not_win_either(self):
        from peer_account.models import Profile

        user = User.objects.create_user("cached")
        _ = user.profile  # cache it, cwl is None on this copy
        Profile.objects.filter(user=user).update(cwl="written-later")

        user.save()

        self.assertEqual(Profile.objects.get(user=user).cwl, "written-later")

    def test_clearing_the_password_lets_cwl_adopt_the_account(self):
        # The migration path for a named legacy account: not archiving, which
        # locks them out, but retiring the password so CWL takes it over with
        # its memberships intact.
        from peer_account.models import Profile

        user = User.objects.create_user("ginstructor", password="legacy!23")
        user.set_unusable_password()
        user.save(update_fields=["password"])
        Profile.objects.filter(user=user).update(cwl="instrg")

        resolved = resolve(
            issuer="https://authentication.ubc.ca",
            student_number=None,
            cwl="instrg",
        )

        self.assertEqual(resolved.pk, user.pk)

    def test_while_the_password_remains_cwl_is_refused(self):
        from peer_account.models import Profile

        user = User.objects.create_user("still-has-one", password="legacy!23")
        Profile.objects.filter(user=user).update(cwl="stillcwl")

        with self.assertRaises(PermissionError):
            resolve(
                issuer="https://authentication.ubc.ca",
                student_number=None,
                cwl="stillcwl",
            )


class IdleFilterTests(TestCase):
    """An account nobody has used for a year can go quietly.

    One somebody used last month deserves a conversation first, especially
    when it is faculty whose only route in is that password.
    """

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone

        self.recent = User.objects.create_user("instrb-2", password="x!23456")
        User.objects.filter(pk=self.recent.pk).update(
            last_login=timezone.now() - timedelta(days=60)
        )
        self.stale = User.objects.create_user("olduser", password="x!23456")
        User.objects.filter(pk=self.stale.pk).update(
            last_login=timezone.now() - timedelta(days=400)
        )
        self.never = User.objects.create_user("Quinn", password="x!23456")

    def test_idle_days_spares_a_recently_used_account(self):
        output = run("--apply", "--idle-days", "365")

        self.recent.refresh_from_db()
        self.stale.refresh_from_db()
        self.assertTrue(self.recent.is_active)
        self.assertFalse(self.stale.is_active)
        self.assertIn("NOT archived", output)

    def test_an_account_that_never_signed_in_always_qualifies(self):
        run("--apply", "--idle-days", "365")

        self.never.refresh_from_db()
        self.assertFalse(self.never.is_active)

    def test_without_the_flag_everything_still_goes(self):
        run("--apply")

        self.recent.refresh_from_db()
        self.assertFalse(self.recent.is_active)

    def test_the_report_shows_an_email_to_identify_people_by(self):
        User.objects.filter(pk=self.recent.pk).update(email="instrb@example.edu")

        self.assertIn("instrb@example.edu", run())


class OnlyNamedTests(TestCase):
    """Archiving a named handful, exactly."""

    def setUp(self):
        self.wanted = User.objects.create_user("instrf", password="x!23456")
        self.also = User.objects.create_user("student_Mixed", password="x!23456")
        self.bystander = User.objects.create_user("instre", password="x!23456")

    def test_only_archives_exactly_those_named(self):
        run("--apply", "--only", "instrf", "--only", "student_Mixed")

        for user in (self.wanted, self.also):
            user.refresh_from_db()
            self.assertFalse(user.is_active)
        self.bystander.refresh_from_db()
        self.assertTrue(self.bystander.is_active)

    def test_it_matches_the_whole_name_not_a_prefix(self):
        longer = User.objects.create_user("instrf-old", password="x!23456")

        run("--apply", "--only", "instrf")

        longer.refresh_from_db()
        self.assertTrue(longer.is_active)

    def test_naming_a_superuser_explicitly_archives_it(self):
        # The superuser guard is there to stop a sweep catching an
        # administrator by accident, not to argue with somebody who typed
        # the name out.
        boss = User.objects.create_superuser("site_admin", "m@x.com", "x!23456")

        run("--apply", "--only", "site_admin")

        boss.refresh_from_db()
        self.assertFalse(boss.is_active)

    def test_only_and_only_prefix_together_are_refused(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            run("--apply", "--only", "instrf", "--only-prefix", "temp_")
