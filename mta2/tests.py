import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

from channels_redis.core import RedisChannelLayer
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse


IN_MEMORY_CHANNELS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


class BrokenChannelLayer:
    async def new_channel(self, prefix="specific."):
        return f"{prefix}broken"

    async def send(self, channel, message):
        raise ConnectionError("sensitive backend detail")


class HealthLivenessTests(SimpleTestCase):
    def test_liveness_is_unauthenticated_and_dependency_free(self):
        with (
            patch(
                "mta2.health._database_is_ready",
                side_effect=AssertionError("liveness touched the database"),
            ),
            patch(
                "mta2.health._channel_layer_is_ready",
                side_effect=AssertionError("liveness touched the channel layer"),
            ),
        ):
            response = self.client.get(reverse("health-live"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok\n")
        self.assertIn("no-cache", response.headers["Cache-Control"])

    def test_health_endpoints_reject_mutating_methods(self):
        self.assertEqual(self.client.post(reverse("health-live")).status_code, 405)
        self.assertEqual(self.client.post(reverse("health-ready")).status_code, 405)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class HealthReadinessTests(TestCase):
    def test_readiness_accepts_database_and_in_memory_channel_backend(self):
        response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ready\n")
        self.assertIn("no-cache", response.headers["Cache-Control"])

    def test_readiness_returns_minimal_503_when_database_is_unavailable(self):
        with patch(
            "mta2.health.connection.cursor",
            side_effect=OperationalError("database host and password detail"),
        ):
            response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable\n")
        self.assertNotIn(b"password", response.content)

    def test_readiness_returns_minimal_503_when_channel_backend_is_unavailable(self):
        with patch(
            "mta2.health.channel_layers.make_backend",
            return_value=BrokenChannelLayer(),
        ):
            response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable\n")
        self.assertNotIn(b"backend", response.content)


class ConfigurationContractTests(SimpleTestCase):
    def test_channels_read_timeout_exceeds_its_blocking_receive_timeout(self):
        host = settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"][0]

        self.assertEqual(host["address"], settings.REDIS_URL)
        self.assertGreater(
            host["socket_timeout"], RedisChannelLayer.brpop_timeout
        )
        self.assertLessEqual(
            host["socket_connect_timeout"], host["socket_timeout"]
        )

    def test_redis_and_allowed_hosts_fail_closed_outside_local_opt_in(self):
        settings_module = importlib.import_module("mta2.settings")
        with (
            patch.dict(
                os.environ,
                {"REDIS_URL": "", "DJANGO_ALLOWED_HOSTS": ""},
            ),
            patch.object(settings_module, "ALLOW_INSECURE_LOCAL_DEFAULTS", False),
        ):
            with self.assertRaises(ImproperlyConfigured):
                settings_module.required_setting(
                    "REDIS_URL",
                    "redis://localhost:6379/0",
                )
            with self.assertRaises(ImproperlyConfigured):
                settings_module.required_setting(
                    "DJANGO_ALLOWED_HOSTS",
                    "localhost,127.0.0.1",
                )

    def test_local_opt_in_uses_only_local_redis_and_host_defaults(self):
        settings_module = importlib.import_module("mta2.settings")
        with (
            patch.dict(
                os.environ,
                {"REDIS_URL": "", "DJANGO_ALLOWED_HOSTS": ""},
            ),
            patch.object(settings_module, "ALLOW_INSECURE_LOCAL_DEFAULTS", True),
        ):
            self.assertEqual(
                settings_module.required_setting(
                    "REDIS_URL",
                    "redis://localhost:6379/0",
                ),
                "redis://localhost:6379/0",
            )
            self.assertEqual(
                settings_module.required_setting(
                    "DJANGO_ALLOWED_HOSTS",
                    "localhost,127.0.0.1",
                ),
                "localhost,127.0.0.1",
            )

            self.assertEqual(
                settings_module.csrf_trusted_origins_for_hosts(
                    ["localhost", "127.0.0.1"]
                ),
                ["http://localhost", "http://127.0.0.1"],
            )

    def test_csrf_origins_are_explicit_https_and_tied_to_allowed_hosts(self):
        settings_module = importlib.import_module("mta2.settings")
        with patch.object(settings_module, "ALLOW_INSECURE_LOCAL_DEFAULTS", False):
            with patch.dict(
                os.environ,
                {"DJANGO_CSRF_TRUSTED_ORIGINS": ""},
            ):
                with self.assertRaises(ImproperlyConfigured):
                    settings_module.csrf_trusted_origins_for_hosts(
                        ["agora.example.invalid"]
                    )
            with patch.dict(
                os.environ,
                {
                    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://dev.example.invalid"
                },
            ):
                with self.assertRaises(ImproperlyConfigured):
                    settings_module.csrf_trusted_origins_for_hosts(
                        ["agora.example.invalid"]
                    )
            with patch.dict(
                os.environ,
                {
                    "DJANGO_CSRF_TRUSTED_ORIGINS": "http://agora.example.invalid"
                },
            ):
                with self.assertRaises(ImproperlyConfigured):
                    settings_module.csrf_trusted_origins_for_hosts(
                        ["agora.example.invalid"]
                    )
            with patch.dict(
                os.environ,
                {
                    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://agora.example.invalid"
                },
            ):
                self.assertEqual(
                    settings_module.csrf_trusted_origins_for_hosts(
                        ["agora.example.invalid"]
                    ),
                    ["https://agora.example.invalid"],
                )

    def test_smtp_backend_requires_explicit_host_sender_and_credentials(self):
        settings_module = importlib.import_module("mta2.settings")
        smtp = settings_module.SMTP_EMAIL_BACKEND
        base_environment = {
            "DJANGO_EMAIL_BACKEND": smtp,
            "DEFAULT_FROM_EMAIL": "agora@example.invalid",
            "EMAIL_HOST": "smtp.example.invalid",
            "EMAIL_PORT": "587",
            "EMAIL_USE_TLS": "true",
            "EMAIL_HOST_USER": "smtp-user",
            "EMAIL_HOST_PASSWORD": "",
        }
        with (
            patch.object(settings_module, "ALLOW_INSECURE_LOCAL_DEFAULTS", False),
            patch.dict(os.environ, base_environment),
        ):
            with self.assertRaises(ImproperlyConfigured):
                settings_module.build_email_configuration()

        complete_environment = {
            **base_environment,
            "EMAIL_HOST_PASSWORD": "smtp-password",
        }
        with (
            patch.object(settings_module, "ALLOW_INSECURE_LOCAL_DEFAULTS", False),
            patch.dict(os.environ, complete_environment),
        ):
            configuration = settings_module.build_email_configuration()

        self.assertEqual(configuration["backend"], smtp)
        self.assertEqual(configuration["host"], "smtp.example.invalid")
        self.assertEqual(configuration["port"], 587)
        self.assertTrue(configuration["use_tls"])
        self.assertEqual(configuration["username"], "smtp-user")
        self.assertEqual(configuration["password"], "smtp-password")

    def test_database_connections_do_not_leak_under_asgi(self):
        # Persistent per-thread connections exhaust PostgreSQL under Daphne;
        # the database must either use a psycopg pool (postgres) or close
        # connections at end of request (CONN_MAX_AGE 0).
        database = settings.DATABASES["default"]
        pool = database.get("OPTIONS", {}).get("pool")
        if pool:
            self.assertEqual(database["CONN_MAX_AGE"], 0)
            self.assertIs(database["CONN_HEALTH_CHECKS"], False)
            self.assertLessEqual(pool["max_size"], 20)
        else:
            self.assertEqual(database["CONN_MAX_AGE"], 0)

    def test_private_upload_permissions_are_configured(self):
        self.assertEqual(settings.FILE_UPLOAD_PERMISSIONS, 0o640)
        self.assertEqual(settings.FILE_UPLOAD_DIRECTORY_PERMISSIONS, 0o750)

    def test_missing_saml_attribute_guidance_has_no_dead_get_logout_link(self):
        module_name = "mta2.saml_settings"
        sys.modules.pop(module_name, None)
        try:
            with patch.dict(
                os.environ,
                {
                    "SAML_SP_PUBLIC_CERT": "test-public-certificate",
                    "SAML_SP_PRIVATE_KEY": "test-private-key",
                    "SAML_IDP_PUBLIC_CERT": "test-idp-certificate",
                },
            ):
                saml_settings = importlib.import_module(module_name)
            message = saml_settings.SAML_USERS_MAP["ubc-ca"]["username"]["error_msg"]
        finally:
            sys.modules.pop(module_name, None)

        self.assertNotIn("/account/saml_logout", message)
        self.assertIn("signed-in page's <strong>Logout</strong> button", message)


class DeploymentHealthProbeTests(SimpleTestCase):
    def test_worker_and_redeploy_use_explicit_health_endpoints(self):
        base_dir = Path(settings.BASE_DIR)
        redeploy = (base_dir / "redeploy.sh").read_text(encoding="utf-8")
        web_unit = (
            base_dir / "deploy" / "systemd" / "agora-web@.service"
        ).read_text(encoding="utf-8")
        runbook = (base_dir / "prod_setup.txt").read_text(encoding="utf-8")

        self.assertIn("/health/ready/", redeploy)
        self.assertIn("${AGORA_PROBE_HOST:-agora.students.cs.ubc.ca}", redeploy)
        self.assertIn("/health/live/", web_unit)
        self.assertIn("/health/live/", runbook)
        self.assertIn("/health/ready/", runbook)


class ProductionPreflightContractTests(SimpleTestCase):
    def test_systemd_runs_fail_closed_preflight(self):
        root = Path(settings.BASE_DIR)
        service = (root / "deploy" / "systemd" / "agora-web@.service").read_text(
            encoding="utf-8"
        )
        preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")

        # Preflight comes from the release, not the old checkout, so that a
        # rollback swaps the gate along with the code it guards.
        self.assertIn(
            "ExecStartPre=/usr/bin/bash /srv/agora/current/deploy/preflight.sh",
            service,
        )
        for required_setting in (
            "DJANGO_SECRET_KEY",
            "DATABASE_URL",
            "DATABASE_CONN_MAX_AGE",
            "REDIS_URL",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "AGORA_PROBE_HOST",
            "DJANGO_STATIC_ROOT",
            "DJANGO_MEDIA_ROOT",
            "DJANGO_EMAIL_BACKEND",
            "DEFAULT_FROM_EMAIL",
        ):
            self.assertIn(required_setting, preflight)

    def test_preflight_rejects_known_unsafe_production_modes(self):
        preflight = (
            Path(settings.BASE_DIR) / "deploy" / "preflight.sh"
        ).read_text(encoding="utf-8")

        for unsafe_contract in (
            "DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS must not be enabled",
            "DJANGO_DEBUG must not be enabled",
            "DJANGO_ALLOWED_HOSTS must contain explicit hostnames only",
            "*insecure*",
            "*replace*",
            "postgresql://*",
            "redis://*",
            "AGORA_PROBE_HOST must be listed in DJANGO_ALLOWED_HOSTS",
            "every CSRF trusted origin host must be in DJANGO_ALLOWED_HOSTS",
            "SMTP backend requires ${smtp_variable}",
            "SMTP credentials must not contain example placeholders",
        ):
            self.assertIn(unsafe_contract, preflight)
        self.assertIn(r"^[0-9]+$", preflight)
        self.assertNotIn("DJANGO_HSTS_SECONDS must be a positive integer", preflight)

    def test_preflight_checks_runtime_and_writable_paths(self):
        preflight = (
            Path(settings.BASE_DIR) / "deploy" / "preflight.sh"
        ).read_text(encoding="utf-8")

        for gate in (
            '! -w "${DJANGO_MEDIA_ROOT}"',
            'log_parent="$(dirname -- "${log_path}")"',
            "manage.py check --deploy",
            "makemigrations --check --dry-run",
            "migrate --check",
            "import mta2.asgi",
        ):
            self.assertIn(gate, preflight)
