from unittest.mock import patch
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from peer_account.forms import UserForm
from peer_account.models import Profile
from peer_course.models import Course, CourseMember
from mta2.views import custom_saml_logout


class LogoutSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="logout-user")
        self.client.force_login(self.user)

    def test_logout_rejects_get(self):
        response = self.client.get(reverse("account:logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post(reverse("account:logout"))

        self.assertEqual(response.status_code, 403)
        self.assertIn("_auth_user_id", csrf_client.session)

    def test_logout_form_supplies_valid_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.get(reverse("course:list"))
        token = response.cookies["csrftoken"].value

        response = csrf_client.post(
            reverse("account:logout"), HTTP_X_CSRFTOKEN=token
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", csrf_client.session)

    def test_temp_instructor_cleanup_runs_only_on_post(self):
        temp_course = Course.objects.create(
            displayname="temp session logout test",
            browsable=True,
            archived=False,
            agora_mode=True,
            stucode="student-code-value-1234567890",
            tascode="ta-code-value-12345678901234",
            instructor_code="instructor-code-value-123456",
        )
        CourseMember.objects.create(
            course=temp_course,
            user=self.user,
            role="instructor",
            active=True,
        )
        real_course = Course.objects.create(
            displayname="Real course must survive",
            browsable=True,
            archived=False,
            agora_mode=True,
            stucode="second-student-code-123456789",
            tascode="second-ta-code-value-123456789",
            instructor_code="second-instructor-code-123456",
        )
        CourseMember.objects.create(
            course=real_course,
            user=self.user,
            role="instructor",
            active=True,
        )
        session = self.client.session
        session["temp"] = True
        session["temp_role"] = "instructor"
        session["temp_course_id"] = temp_course.pk
        session.save()

        response = self.client.get(reverse("account:logout"))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(Course.objects.filter(pk=temp_course.pk).exists())
        self.assertTrue(Course.objects.filter(pk=real_course.pk).exists())

        response = self.client.post(reverse("account:logout"))

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Course.objects.filter(pk=temp_course.pk).exists())
        self.assertTrue(Course.objects.filter(pk=real_course.pk).exists())

    @override_settings(SAML_ENTITY_ID="https://agora.example.invalid/")
    @patch("mta2.views._saml_auth")
    def test_csrf_protected_logout_preserves_saml_redirect_flow(self, saml_builder):
        saml = saml_builder.return_value
        saml.logout.return_value = "https://idp.example.invalid/logout?SAMLRequest=signed"
        saml.get_last_request_id.return_value = "request-id"
        session = self.client.session
        session["saml_login"] = True
        session["saml_provider"] = "ubc-ca"
        session["samlNameId"] = "opaque-subject"
        session["samlSessionIndex"] = "opaque-session"
        session.save()

        response = self.client.post(reverse("account:logout"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, saml.logout.return_value)
        saml.logout.assert_called_once()
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_legacy_get_cannot_initiate_saml_logout(self):
        session = self.client.session
        session["saml_login"] = True
        session.save()

        response = self.client.get(reverse("legacy_saml_root") + "?slo")

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)

    def test_direct_get_cannot_initiate_custom_saml_logout(self):
        request = RequestFactory().get("/internal/saml/logout/")

        response = custom_saml_logout(request, "ubc-ca")

        self.assertEqual(response.status_code, 405)


class SamlLoginInitiationSecurityTests(TestCase):
    def setUp(self):
        self.login_url = reverse(
            "custom_saml_login",
            kwargs={"provider": "ubc-ca"},
        )

    def test_direct_and_legacy_cross_site_gets_are_405_and_do_not_mutate_session(self):
        direct = self.client.get(
            self.login_url,
            {"next": "/course/list/"},
            HTTP_ORIGIN="https://attacker.example.invalid",
        )
        legacy = self.client.get(
            reverse("legacy_saml_root"),
            {"login": "", "next": "/course/list/"},
            HTTP_ORIGIN="https://attacker.example.invalid",
        )
        legacy_post = self.client.post(
            reverse("legacy_saml_root") + "?login",
            {"next": "/course/list/"},
            HTTP_ORIGIN="https://attacker.example.invalid",
        )

        self.assertEqual(direct.status_code, 405)
        self.assertEqual(legacy.status_code, 405)
        self.assertEqual(legacy_post.status_code, 405)
        self.assertNotIn("saml_request_id", self.client.session)

    def test_login_page_uses_csrf_protected_named_post_form(self):
        response = self.client.get(reverse("account:login"))

        self.assertContains(response, f'action="{self.login_url}"')
        self.assertContains(response, 'method="post"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, "?login&next=")

        legacy_template = (
            Path(settings.BASE_DIR) / "peer_account" / "templates" / "cwl-login.html"
        ).read_text(encoding="utf-8")
        self.assertIn("{% url 'custom_saml_login' provider='ubc-ca' %}", legacy_template)
        self.assertIn('{% csrf_token %}', legacy_template)
        self.assertIn('method="post"', legacy_template)
        self.assertNotIn("saml2_pro_auth:login", legacy_template)

    @patch("mta2.views._saml_auth")
    def test_csrf_post_stores_request_state_and_redirects(self, saml_builder):
        saml = saml_builder.return_value
        saml.login.return_value = "https://idp.example.invalid/sso?request=signed"
        saml.get_last_request_id.return_value = "opaque-login-request-id"
        csrf_client = Client(enforce_csrf_checks=True)
        login_page = csrf_client.get(reverse("account:login"))
        token = login_page.cookies["csrftoken"].value

        rejected = Client(enforce_csrf_checks=True).post(
            self.login_url,
            {"next": "/course/list/"},
        )
        response = csrf_client.post(
            self.login_url,
            {"next": "/course/list/"},
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, saml.login.return_value)
        # The in-flight request id travels in a signed cookie rather than the
        # session: the assertion returns by cross-site POST, and a
        # SameSite=Lax session cookie is not sent on those.
        from django.test import RequestFactory

        from mta2.views import SAML_TRANSACTION_COOKIE, _read_saml_request_id

        self.assertIn(SAML_TRANSACTION_COOKIE, response.cookies)
        carrier = RequestFactory().post("/sso/saml/?acs")
        carrier.COOKIES[SAML_TRANSACTION_COOKIE] = response.cookies[
            SAML_TRANSACTION_COOKIE
        ].value
        self.assertEqual(
            _read_saml_request_id(carrier), "opaque-login-request-id"
        )
        saml.login.assert_called_once_with(return_to="/course/list/")


class ResponseSecurityHeaderTests(TestCase):
    def test_application_responses_include_compatibility_csp(self):
        response = self.client.get(reverse("account:login"))

        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("script-src 'self' 'unsafe-inline'", policy)
        self.assertNotIn("'unsafe-eval'", policy)


class ProfileEditReadOnlyGetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="profile-edit-user",
            email="profile@example.invalid",
            first_name="Profile",
            last_name="User",
        )
        self.client.force_login(self.user)
        # force_login updates last_login, which exercises the legacy profile
        # post-save signal. Remove the row afterward so the request itself is
        # the only possible creator under test.
        Profile.objects.filter(user=self.user).delete()
        self.url = reverse("account:edit", kwargs={"uid": self.user.pk})

    def test_get_does_not_create_missing_profile(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Profile.objects.filter(user=self.user).exists())

    def test_valid_post_creates_missing_profile(self):
        response = self.client.post(
            self.url,
            {
                "username": self.user.username,
                "email": self.user.email,
                "first_name": self.user.first_name,
                "last_name": self.user.last_name,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())


class DeploymentConfigurationTests(SimpleTestCase):
    def test_timezone_assets_are_current_and_integrity_pinned(self):
        template = (
            Path(settings.BASE_DIR) / "peer_course" / "templates" / "agora.html"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "moment.js/2.30.1/moment.min.js", template
        )
        self.assertIn(
            "sha384-KIix3a0qkeD2RPwPvpkJ+Knc91vkmDI+i2c7phIO+EfV3dpfDXIGSqQjpIaJXlR9",
            template,
        )
        self.assertIn(
            "moment-timezone/0.6.3/moment-timezone-with-data.min.js", template
        )
        self.assertIn(
            "sha384-iMjDoiV3VPG7+mDyv5yJCAV6VuLoyXqW23V9cw20Kz4wzEdVCxApExC0/Tfc0Lup",
            template,
        )
        self.assertNotIn("moment.js/2.29.1/", template)
        self.assertNotIn("moment-timezone/0.5.34/", template)

    def test_nginx_logs_no_query_and_does_not_serve_media(self):
        nginx = (Path(settings.BASE_DIR) / "nginx.conf").read_text(encoding="utf-8")
        active = "\n".join(
            line for line in nginx.splitlines() if not line.lstrip().startswith("#")
        )
        access_log_format = active.split("upstream agora_asgi", 1)[0]

        self.assertEqual(active.count("{"), active.count("}"))
        self.assertEqual(
            active.count(
                "access_log /var/log/nginx/agora.access.log agora_no_query;"
            ),
            2,
        )
        self.assertNotIn("$request_uri", access_log_format)
        self.assertNotRegex(access_log_format, r"\$request(?:\s|['\"])")
        self.assertNotIn("location /media/", active)

    def test_systemd_timer_and_daphne_logging_are_hardened(self):
        unit_dir = Path(settings.BASE_DIR) / "deploy" / "systemd"
        web = (unit_dir / "agora-web@.service").read_text(encoding="utf-8")
        web_active = "\n".join(
            line for line in web.splitlines() if not line.lstrip().startswith("#")
        )
        service = (unit_dir / "agora-live-sessions.service").read_text(
            encoding="utf-8"
        )
        timer = (unit_dir / "agora-live-sessions.timer").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("--access-log", web_active)
        self.assertIn("--verbosity 0", web_active)
        for required in (
            "Type=oneshot",
            "manage.py manage_live_sessions",
            "TimeoutStartSec=50",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "StandardOutput=journal",
        ):
            self.assertIn(required, service)
        for required in (
            "OnCalendar=*-*-* *:*:00",
            "AccuracySec=1s",
            "Persistent=true",
            "WantedBy=timers.target",
        ):
            self.assertIn(required, timer)


def _signup_data(**overrides):
    data = {
        "username": "jsmith",
        "password": "correct-horse-battery-staple",
        "email": "jsmith@example.com",
        "first_name": "Jim",
        "last_name": "Smith",
    }
    data.update(overrides)
    return data


@override_settings(SIGNUP_ENABLED=True)
class SignupUniquenessTests(TestCase):
    """One person, one account.

    A student who never received a reset email signed up seven times with the
    same address and case-variant usernames; the reset view then mailed seven
    links for seven usernames. The form now refuses the duplicates up front.
    """

    def setUp(self):
        User.objects.create_user(username="jsmith", email="Jsmith@example.com", password="pw")

    def test_duplicate_email_is_rejected_case_insensitively(self):
        form = UserForm(data=_signup_data(username="jsmith1", email="jsmith@example.com"))
        self.assertFalse(form.is_valid())
        self.assertIn("already exists", form.errors["email"][0])
        self.assertIn("Reset here", form.errors["email"][0])

    def test_duplicate_username_is_rejected_case_insensitively(self):
        form = UserForm(data=_signup_data(username="Jsmith", email="other@example.com"))
        self.assertFalse(form.is_valid())
        self.assertIn("already exists", form.errors["username"][0])

    def test_fresh_email_and_username_are_accepted(self):
        form = UserForm(data=_signup_data(username="someone", email="someone@example.com"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_signup_view_does_not_create_second_account_for_same_email(self):
        response = self.client.post(
            reverse("account:signup"),
            _signup_data(username="Jumbo", email="JSMITH@example.com"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email__iexact="jsmith@example.com").count(), 1)
        self.assertContains(response, "already exists")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetEmailTests(TestCase):
    def test_reset_request_sends_exactly_one_email_for_one_account(self):
        User.objects.create_user(username="jsmith", email="jsmith@example.com", password="pw")
        response = self.client.post(reverse("account:password_reset"), {"email": "Jsmith@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/account/reset/", mail.outbox[0].body)

    def test_inactive_duplicate_accounts_do_not_get_reset_emails(self):
        User.objects.create_user(username="jsmith", email="jsmith@example.com", password="pw")
        User.objects.create_user(username="Jumbo", email="jsmith@example.com", password="pw", is_active=False)
        self.client.post(reverse("account:password_reset"), {"email": "jsmith@example.com"})
        self.assertEqual(len(mail.outbox), 1)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="agora-noreply@example.com",
)
class PasswordResetLinkTests(TestCase):
    """The reset email must be usable: right host, right sender, and it names
    the account, because "which username was that?" is the question people
    are really asking when they reach for the reset link."""

    def setUp(self):
        from django.contrib.sites.models import Site
        Site.objects.update_or_create(id=1, defaults={"domain": "example.com", "name": "example.com"})
        User.objects.create_user(username="jsmith", email="jsmith@example.com", password="pw", first_name="Jim")

    def test_link_uses_the_request_host_not_the_sites_table(self):
        self.client.post(reverse("account:password_reset"), {"email": "jsmith@example.com"})
        body = mail.outbox[0].body
        self.assertIn("://testserver/account/reset/", body)
        self.assertNotIn("example.com/account/reset/", body)

    def test_email_names_the_username_and_comes_from_the_configured_sender(self):
        self.client.post(reverse("account:password_reset"), {"email": "jsmith@example.com"})
        message = mail.outbox[0]
        self.assertIn("Your username is:  jsmith", message.body)
        self.assertEqual(message.from_email, "agora-noreply@example.com")
        self.assertEqual(message.to, ["jsmith@example.com"])

    def test_the_link_in_the_email_opens_a_valid_reset_form(self):
        import re
        self.client.post(reverse("account:password_reset"), {"email": "jsmith@example.com"})
        link = re.search(r"https?://testserver(/account/reset/\S+/)", mail.outbox[0].body).group(1)
        response = self.client.get(link, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm new password")


LOCMEM_CACHE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "login-tests"}
}


@override_settings(CACHES=LOCMEM_CACHE)
class LoginByEmailOrCaseTests(TestCase):
    def setUp(self):
        User.objects.create_user(username="jsmith", email="jsmith@example.com", password="correct-horse-battery")

    def _login(self, name, password="correct-horse-battery"):
        return self.client.post(reverse("account:login"), {"stid": name, "password": password})

    def _signed_in_as(self):
        uid = self.client.session.get("_auth_user_id")
        return User.objects.get(pk=uid).username if uid else None

    def test_exact_username_still_works(self):
        self._login("jsmith")
        self.assertEqual(self._signed_in_as(), "jsmith")

    def test_email_address_signs_in(self):
        self._login("JSmith@Example.com")
        self.assertEqual(self._signed_in_as(), "jsmith")

    def test_case_variant_signs_in_when_it_is_unambiguous(self):
        self._login("JSMITH")
        self.assertEqual(self._signed_in_as(), "jsmith")

    def test_case_variant_is_not_guessed_between_two_accounts(self):
        User.objects.create_user(username="Jsmith", email="other@example.com", password="something-else-entirely")
        self._login("JSMITH")
        self.assertIsNone(self._signed_in_as())
        # ...but each exact spelling still reaches its own account.
        self._login("Jsmith", "something-else-entirely")
        self.assertEqual(self._signed_in_as(), "Jsmith")

    def test_inactive_account_is_not_resolved_by_email(self):
        User.objects.filter(username="jsmith").update(is_active=False)
        response = self._login("jsmith@example.com")
        self.assertIsNone(self._signed_in_as())
        self.assertIn("success=false", response["Location"])

    def test_wrong_password_with_email_fails(self):
        response = self._login("jsmith@example.com", "nope")
        self.assertIsNone(self._signed_in_as())
        self.assertIn("success=false", response["Location"])


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="jsmith", email="jsmith@example.com", password="old-password-123")
        self.url = reverse("account:password_change")

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account:login"), response["Location"])

    def test_page_is_linked_from_the_account_pages(self):
        self.client.force_login(self.user)
        for name in ("account:view", "account:edit"):
            response = self.client.get(reverse(name, kwargs={"uid": self.user.id}))
            self.assertContains(response, self.url, msg_prefix=name)

    def test_a_password_account_can_change_its_password_and_stays_signed_in(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {
            "old_password": "old-password-123",
            "new_password1": "brand-new-password-456",
            "new_password2": "brand-new-password-456",
        })
        self.assertRedirects(response, reverse("account:view", kwargs={"uid": self.user.id}), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-new-password-456"))
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.user.id))
        response = self.client.get(response["Location"])
        self.assertContains(response, "Your password has been changed.")

    def test_wrong_current_password_changes_nothing(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {
            "old_password": "not-it",
            "new_password1": "brand-new-password-456",
            "new_password2": "brand-new-password-456",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password-123"))

    def test_weak_new_password_is_refused(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {
            "old_password": "old-password-123",
            "new_password1": "short",
            "new_password2": "short",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "too short")

    def test_a_cwl_account_has_nothing_to_change(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("account:view", kwargs={"uid": self.user.id}), fetch_redirect_response=False)
        response = self.client.get(reverse("account:view", kwargs={"uid": self.user.id}))
        self.assertNotContains(response, self.url)
