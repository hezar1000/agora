"""Guards for the failures found during the MTA cutover.

Each of these was a real defect there. Agora carried the same code, would have
failed the same way, and none of it is reachable by an ordinary test of the
application: they live in the browser, the identity provider, or systemd.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings


class LoggingReachesSomewhereTests(SimpleTestCase):
    """A logger with no handler discards its records silently."""

    def test_a_root_handler_catches_unnamed_loggers(self):
        self.assertIn("root", settings.LOGGING)
        self.assertTrue(settings.LOGGING["root"]["handlers"])

    def test_an_application_logger_resolves_to_a_handler(self):
        import logging

        for name in ("mta2.views", "peer_home.views", "peer_course.views"):
            with self.subTest(logger=name):
                logger = logging.getLogger(name)
                handlers, current = [], logger
                while current:
                    handlers.extend(current.handlers)
                    current = current.parent if current.propagate else None
                self.assertTrue(handlers, "%s would discard its records" % name)


class SamlFormActionPolicyTests(SimpleTestCase):
    """form-action is enforced against redirect targets."""

    IDP = {
        "SAML2_INTEGRATION_ENABLED": "1",
        "SAML_ENTITY_ID": "https://agora.example.edu/",
        "SAML_IDP_ENTITY_ID": "https://idp.example.edu",
        "SAML_IDP_SSO_URL": "https://idp.example.edu/idp/profile/SAML2/Redirect/SSO",
        "SAML_IDP_SLO_URL": "https://idp.example.edu/idp/profile/Logout",
        "SAML_SP_PUBLIC_CERT_FILE": "/dev/null",
        "SAML_SP_PRIVATE_KEY_FILE": "/dev/null",
        "SAML_IDP_PUBLIC_CERT_FILE": "/dev/null",
    }

    def _form_action(self, **overrides):
        env = dict(os.environ)
        env["DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS"] = "1"
        env["DJANGO_SETTINGS_MODULE"] = "mta2.settings"
        env.update(self.IDP)
        env.update(overrides)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import django; django.setup();"
                "from django.conf import settings;"
                "print(settings.CONTENT_SECURITY_POLICY)",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(settings.BASE_DIR),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for directive in result.stdout.strip().split("; "):
            if directive.startswith("form-action "):
                return directive
        self.fail("no form-action directive")

    def test_the_provider_may_receive_the_sign_in_redirect(self):
        """The provider is configured in saml_settings, not by environment.

        The origin is therefore read from that configuration rather than
        supplied by this test, which is the point: whatever provider a
        deployment names is the one allowed.
        """
        directive = self._form_action()
        self.assertIn("'self'", directive)
        self.assertIn("https://authentication.ubc.ca", directive)

    def test_only_the_origin_is_allowed_not_the_path(self):
        self.assertNotIn("/idp/profile", self._form_action())

    def test_without_saml_the_policy_stays_closed(self):
        self.assertEqual(
            self._form_action(SAML2_INTEGRATION_ENABLED="0"), "form-action 'self'"
        )


class SamlDestinationUrlTests(TestCase):
    """Strict mode requires the assertion Destination to match our own URL."""

    def _self_url(self, **meta):
        from django.test import RequestFactory
        from onelogin.saml2.utils import OneLogin_Saml2_Utils

        from mta2.views import _build_saml_req

        request = RequestFactory().post("/sso/saml/?acs")
        request.META["HTTP_HOST"] = "agora.students.cs.ubc.ca"
        request.META["SERVER_PORT"] = "8000"
        request.META.update(meta)
        with override_settings(
            ALLOWED_HOSTS=["agora.students.cs.ubc.ca"],
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ):
            return OneLogin_Saml2_Utils.get_self_url_no_query(_build_saml_req(request))

    def test_the_application_port_never_reaches_the_destination_url(self):
        url = self._self_url(HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(url, "https://agora.students.cs.ubc.ca/sso/saml/")
        self.assertNotIn(":8000", url)

    def test_an_explicit_forwarded_port_is_honoured(self):
        self.assertEqual(
            self._self_url(
                HTTP_X_FORWARDED_PROTO="https", HTTP_X_FORWARDED_PORT="443"
            ),
            "https://agora.students.cs.ubc.ca/sso/saml/",
        )


class SamlCrossSiteTransactionTests(TestCase):
    """The provider returns the assertion by cross-site POST."""

    def test_the_correlation_cookie_can_cross_sites(self):
        from django.http import HttpResponseRedirect

        from mta2.views import SAML_TRANSACTION_COOKIE, _store_saml_request_id

        with override_settings(SESSION_COOKIE_SECURE=True):
            response = _store_saml_request_id(HttpResponseRedirect("/"), "req-1")
        cookie = response.cookies[SAML_TRANSACTION_COOKIE]
        self.assertEqual(cookie["samesite"], "None")
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])

    def test_the_session_cookie_itself_stays_lax(self):
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")

    def test_a_round_trip_recovers_the_request_id(self):
        from django.http import HttpResponseRedirect
        from django.test import RequestFactory

        from mta2.views import _read_saml_request_id, _store_saml_request_id

        with override_settings(SESSION_COOKIE_SECURE=True):
            response = _store_saml_request_id(HttpResponseRedirect("/"), "req-1")
        request = RequestFactory().post("/sso/saml/?acs")
        request.COOKIES["saml_txn"] = response.cookies["saml_txn"].value

        self.assertEqual(_read_saml_request_id(request), "req-1")

    def test_a_tampered_cookie_is_refused(self):
        from django.test import RequestFactory

        from mta2.views import _read_saml_request_id

        request = RequestFactory().post("/sso/saml/?acs")
        request.COOKIES["saml_txn"] = "not-a-valid-signed-value"
        self.assertIsNone(_read_saml_request_id(request))


class SamlSignatureRequirementTests(SimpleTestCase):
    """A signature is still required, matching what the provider sends."""

    def _source(self):
        return (
            Path(settings.BASE_DIR) / "mta2" / "saml_settings.py"
        ).read_text(encoding="utf-8")

    def test_the_response_signature_is_still_required(self):
        self.assertIn('"wantMessagesSigned": True', self._source())

    def test_assertions_stay_encrypted(self):
        self.assertIn('"wantAssertionsEncrypted": True', self._source())

    def test_assertion_signing_is_not_demanded_of_this_provider(self):
        self.assertIn('"wantAssertionsSigned": False', self._source())

    def test_the_reason_is_recorded_beside_the_setting(self):
        self.assertIn("does not sign that Assertion separately", self._source())


class DeploymentPathConsistencyTests(SimpleTestCase):
    """The media root and the unit that grants write access must agree.

    ProtectSystem=strict makes ReadWritePaths load-bearing. If the configured
    media root sits outside it, the service starts and then fails every
    upload, which reads as data loss rather than a permissions problem.
    """

    def _unit(self, name):
        return (
            Path(settings.BASE_DIR) / "deploy" / "systemd" / name
        ).read_text(encoding="utf-8")

    def _example_value(self, key):
        source = (
            Path(settings.BASE_DIR) / ".env.example"
        ).read_text(encoding="utf-8")
        for line in source.splitlines():
            if line.startswith("%s=" % key):
                return line.split("=", 1)[1].strip().strip("'\"")
        self.fail("%s missing from .env.example" % key)

    def test_every_unit_grants_write_to_the_configured_media_root(self):
        media_root = self._example_value("DJANGO_MEDIA_ROOT")
        for name in ("agora-web@.service", "agora-live-sessions.service"):
            with self.subTest(unit=name):
                self.assertIn(media_root, self._unit(name))

    def test_units_run_the_release_rather_than_the_old_checkout(self):
        """Every path, not just ExecStart.

        ExecStartPre was missed once and pointed at the old checkout, so the
        worker pool refused to start with "No such file or directory" on a
        preflight script that lives in the release.
        """
        for name in (
            "agora-web@.service",
            "agora-live-sessions.service",
            "agora-live-sessions.timer",
        ):
            with self.subTest(unit=name):
                unit = self._unit(name)
                self.assertNotIn("/home/agora/agora/", unit)

    def test_the_web_unit_runs_preflight_from_the_release(self):
        unit = self._unit("agora-web@.service")
        self.assertIn("/srv/agora/current/deploy/preflight.sh", unit)
        self.assertIn("/srv/agora/current/.venv/bin/", unit)

    def test_uploads_do_not_live_under_the_static_root(self):
        static_root = self._example_value("DJANGO_STATIC_ROOT")
        media_root = self._example_value("DJANGO_MEDIA_ROOT")
        self.assertFalse(media_root.startswith(static_root.rstrip("/") + "/"))
        self.assertNotEqual(media_root, static_root)


class UnitGroupTests(SimpleTestCase):
    """systemd does not add supplementary groups once Group= is set.

    The secrets under /etc/agora are root:agora 0640, so any other primary
    group loses access to them and the service fails to start. This was a live
    failure on MTA.
    """

    def test_units_run_with_the_group_that_owns_the_secrets(self):
        for name in ("agora-web@.service", "agora-live-sessions.service"):
            unit = (
                Path(settings.BASE_DIR) / "deploy" / "systemd" / name
            ).read_text(encoding="utf-8")
            with self.subTest(unit=name):
                self.assertIn("\nGroup=agora", unit)
                self.assertNotIn("\nGroup=www-data", unit)


class PreflightUsesTheReleaseTests(SimpleTestCase):
    """preflight runs from the release, so it must use the release's venv.

    It hardcoded ./agora_env/bin/python, the old checkout's virtualenv name.
    Running from /srv/agora/current that path does not exist, and the failure
    surfaced as "Python 3.11 or newer is required" rather than as a missing
    interpreter.
    """

    def _source(self):
        return (
            Path(settings.BASE_DIR) / "deploy" / "preflight.sh"
        ).read_text(encoding="utf-8")

    def test_it_uses_the_release_virtualenv(self):
        source = self._source()
        self.assertIn("./.venv/bin/python", source)
        self.assertNotIn("agora_env", source)

    def test_a_missing_interpreter_says_so(self):
        self.assertIn("No interpreter at ./.venv/bin/python", self._source())


class PreflightLocatesItsOwnReleaseTests(SimpleTestCase):
    """A hardcoded cd silently overrides the unit's WorkingDirectory.

    preflight ran `cd /home/agora/agora`, so it checked the old checkout while
    the unit correctly pointed at the release, and reported a missing
    interpreter for a release whose virtualenv was present all along.
    """

    def _source(self):
        return (
            Path(settings.BASE_DIR) / "deploy" / "preflight.sh"
        ).read_text(encoding="utf-8")

    def test_it_resolves_its_own_directory(self):
        self.assertIn('cd "$(dirname "$(readlink -f "$0")")/.."', self._source())

    def test_it_does_not_hardcode_a_checkout(self):
        source = self._source()
        self.assertNotIn("cd /home/agora/agora", source)
        self.assertNotIn("cd /srv/agora", source)

    def test_it_actually_lands_on_the_release_root(self):
        """Run the script's own expression, with $0 as the script path."""
        import subprocess

        release_root = Path(settings.BASE_DIR)
        script = release_root / "deploy" / "preflight.sh"
        result = subprocess.run(
            [
                "bash",
                "-c",
                'cd "$(dirname "$(readlink -f "$0")")/.." && pwd -P',
                str(script),
            ],
            capture_output=True,
            text=True,
            cwd="/",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            Path(result.stdout.strip()).resolve(), release_root.resolve()
        )


class WorkerPoolPortTests(SimpleTestCase):
    """The new pool must not contend with the deployed one.

    The deployed stack serves 8080-8083 under Supervisor. Binding those either
    fails or leaves the readiness probe interrogating the old application,
    which answers 404 for a health route it does not have.
    """

    def _target(self):
        return (
            Path(settings.BASE_DIR) / "deploy" / "systemd" / "agora-web.target"
        ).read_text(encoding="utf-8")

    def test_the_pool_avoids_the_deployed_ports(self):
        target = self._target()
        for port in ("8080", "8081", "8082", "8083"):
            with self.subTest(port=port):
                self.assertNotIn("agora-web@%s.service" % port, target)

    def test_the_pool_is_four_workers(self):
        target = self._target()
        for port in ("8090", "8091", "8092", "8093"):
            with self.subTest(port=port):
                self.assertIn("agora-web@%s.service" % port, target)


class AgoraIdentityBindingTests(TestCase):
    """UBC issues a transient NameID and releases the student number to
    students only. Binding on the NameID refuses every returning user on
    their second sign-in; requiring the student number refuses all staff."""

    def _resolve(self, **kwargs):
        from mta2.views import _resolve_saml_user

        return _resolve_saml_user(issuer="https://idp.example", **kwargs)

    def test_a_returning_user_keeps_one_identity(self):
        from peer_account.models import FederatedIdentity

        first, _ = self._resolve(student_number="12345678", cwl="astudent")
        second, _ = self._resolve(student_number="12345678", cwl="astudent")

        self.assertEqual(first, second)
        self.assertEqual(FederatedIdentity.objects.filter(user=first).count(), 1)
        self.assertEqual(
            FederatedIdentity.objects.get(user=first).subject, "astudent"
        )

    def test_staff_without_a_student_number_can_sign_in(self):
        user, _ = self._resolve(student_number="", cwl="instrb")

        self.assertEqual(user.username, "instrb")
        self.assertFalse(user.has_usable_password())

    def test_a_signed_student_number_adopts_a_pre_sso_password_account(self):
        from django.contrib.auth.models import User

        User.objects.create_user(username="12345678", password="pre-sso")

        user, _ = self._resolve(student_number="12345678", cwl="astudent")

        user.refresh_from_db()
        self.assertEqual(user.username, "12345678")
        self.assertFalse(user.has_usable_password())

    def test_a_claimable_cwl_does_not_adopt_a_password_account(self):
        from django.contrib.auth.models import User

        target = User.objects.create_user(username="attacker-cwl", password="local")

        resolved, _ = self._resolve(student_number="", cwl="attacker-cwl")

        # The account keeps its password and its rows; the assertion gets a
        # fresh one. It is renamed, which costs it a label and nothing else --
        # the property that matters is that the sign-in never lands on it.
        self.assertNotEqual(resolved.pk, target.pk)
        target.refresh_from_db()
        self.assertTrue(target.has_usable_password())
        self.assertEqual(target.username, "attacker-cwl-2")


class LegacyStaffLinkingTests(TestCase):
    """Every named instructor account on Agora predates SSO and carries a
    password, so reaching one requires an administrator-recorded link. These
    accounts are the reason the CWL path exists at all."""

    def _resolve(self, **kwargs):
        from mta2.views import _resolve_saml_user

        return _resolve_saml_user(issuer="https://idp.example", **kwargs)

    def _instructor(self, username):
        from django.contrib.auth.models import User

        return User.objects.create_user(username=username, password="pre-sso")

    def test_an_unlinked_instructor_still_gets_in_under_their_cwl(self):
        # Their legacy account holds the name their CWL would take. The
        # provider asserts the CWL, so its holder has the stronger claim: the
        # new account takes the name and the legacy one is renamed away.
        legacy = self._instructor("instrb")

        resolved, _ = self._resolve(student_number="", cwl="instrb")

        self.assertNotEqual(resolved.pk, legacy.pk)
        self.assertEqual(resolved.username, "instrb")
        legacy.refresh_from_db()
        self.assertEqual(legacy.username, "instrb-2")
        self.assertTrue(legacy.has_usable_password())

    def test_the_legacy_account_is_never_handed_over(self):
        legacy = self._instructor("instrb")

        first, _ = self._resolve(student_number="", cwl="instrb")
        second, _ = self._resolve(student_number="", cwl="instrb")

        # Same person, same account, and never the one they used to own.
        self.assertEqual(first.pk, second.pk)
        self.assertNotEqual(first.pk, legacy.pk)

    def test_linking_lets_the_instructor_reach_their_own_account(self):
        from django.core.management import call_command

        instructor = self._instructor("PatSampleton")
        call_command(
            "link_saml_identity", username="PatSampleton", cwl="pat"
        )

        resolved, _ = self._resolve(student_number="", cwl="pat")

        self.assertEqual(resolved.pk, instructor.pk)
        self.assertFalse(resolved.has_usable_password())

    def test_a_link_survives_the_second_sign_in(self):
        from peer_account.models import FederatedIdentity

        instructor = self._instructor("instrg")
        from django.core.management import call_command

        call_command("link_saml_identity", username="instrg", cwl="instrg")

        first, _ = self._resolve(student_number="", cwl="instrg")
        second, _ = self._resolve(student_number="", cwl="instrg")

        self.assertEqual(first.pk, instructor.pk)
        self.assertEqual(second.pk, instructor.pk)
        self.assertEqual(
            FederatedIdentity.objects.filter(user=instructor).count(), 1
        )

    def test_a_passwordless_username_match_is_not_adopted_either(self):
        # A retired password is not evidence of a link. The account is renamed
        # aside, never resolved to, whoever holds that CWL.
        from django.contrib.auth.models import User

        squatter = User.objects.create_user(username="instra")
        squatter.set_unusable_password()
        squatter.save(update_fields=["password"])

        resolved, _ = self._resolve(student_number="", cwl="instra")

        self.assertNotEqual(resolved.pk, squatter.pk)
        self.assertEqual(resolved.username, "instra")
        squatter.refresh_from_db()
        self.assertEqual(squatter.username, "instra-2")

    def test_an_sso_account_is_never_displaced(self):
        incumbent, _ = self._resolve(student_number="", cwl="shared")
        self.assertEqual(incumbent.username, "shared")

        from mta2.views import _resolve_saml_user

        with self.assertRaises(PermissionError):
            _resolve_saml_user(
                issuer="https://other-idp.example",
                student_number="",
                cwl="shared",
            )

        incumbent.refresh_from_db()
        self.assertEqual(incumbent.username, "shared")

    def test_linking_refuses_a_cwl_already_recorded_elsewhere(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        self._instructor("dsample")
        self._instructor("instrd")
        call_command("link_saml_identity", username="dsample", cwl="instrd")

        with self.assertRaises(CommandError):
            call_command("link_saml_identity", username="instrd", cwl="instrd")


class ProfileSignalRespectsRawSavesTests(TestCase):
    """Fixture loading replays serialised rows and supplies its own Profiles.

    A post_save receiver that creates one anyway collides on the one-to-one
    constraint and aborts the entire load, which is how the first attempt at
    migrating Agora's records failed.
    """

    def test_a_raw_save_does_not_create_a_second_profile(self):
        from django.contrib.auth.models import User
        from django.db.models.signals import post_save

        from peer_account.models import Profile

        user = User(username="raw-load", pk=9001)
        # What loaddata does: save the row as-is, then load its Profile.
        post_save.send(
            sender=User, instance=user, created=True, raw=True, using="default"
        )
        self.assertEqual(Profile.objects.filter(user_id=9001).count(), 0)

    def test_an_ordinary_save_still_creates_one(self):
        from django.contrib.auth.models import User

        from peer_account.models import Profile

        user = User.objects.create_user(username="ordinary")
        self.assertEqual(Profile.objects.filter(user=user).count(), 1)
