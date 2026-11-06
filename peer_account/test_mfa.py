"""A password account cannot use the site without a second factor.

The rule under test is one line -- if you can sign in with a password, you
need a second factor -- and most of these tests are about the ways round it
rather than the happy path. A second factor that the admin login walks past,
or that a password alone can re-enrol, is decoration.
"""

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import (
    Client,
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from peer_account import mfa
from peer_account.middleware import websocket_mfa_is_verified
from peer_account.views import _too_many_attempts
from peer_course.models import Course, CourseMember


def code_for(device):
    """The six digits the authenticator app would be showing."""
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = __import__("time").time()
    return format(totp.token(), "06d")


@override_settings(MFA_ENFORCED=True, SIGNUP_ENABLED=True)
class EnrolmentIsRequiredTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("has-a-password", password="hunter2!x")
        self.client = Client()

    def test_signing_up_lands_on_the_enrolment_page(self):
        response = Client().post(reverse("account:signup"), {
            "username": "newcomer", "password": "correct horse battery",
            "email": "newcomer@example.com",
            "first_name": "New", "last_name": "Comer",
        })

        self.assertRedirects(
            response, reverse("account:mfa_setup"), fetch_redirect_response=False
        )

    def test_signup_page_keeps_the_requested_destination(self):
        destination = "/course/list/?view=mine"

        response = self.client.get(
            reverse("account:signup"), {"next": destination}
        )

        self.assertContains(
            response,
            f'name="next" value="{destination.replace("&", "&amp;")}"',
        )

    def test_a_password_account_is_held_at_enrolment(self):
        self.client.login(username="has-a-password", password="hunter2!x")

        response = self.client.get("/course/list/")

        self.assertRedirects(
            response,
            f"{reverse('account:mfa_setup')}?next=%2Fcourse%2Flist%2F",
            fetch_redirect_response=False,
        )

    def test_a_wrong_code_does_not_enrol_anything(self):
        self.client.login(username="has-a-password", password="hunter2!x")
        self.client.get(reverse("account:mfa_setup"))

        response = self.client.post(reverse("account:mfa_setup"), {"token": "000000"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            TOTPDevice.objects.filter(user=self.user, confirmed=True).exists()
        )

    def test_the_right_code_enrols(self):
        self.client.login(username="has-a-password", password="hunter2!x")
        self.client.get(reverse("account:mfa_setup"))
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)

        response = self.client.post(
            reverse("account:mfa_setup"), {"token": code_for(device)}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            TOTPDevice.objects.filter(user=self.user, confirmed=True).exists()
        )

    def test_guessing_is_throttled(self):
        # django-otp refuses further attempts for a cooling-off period after
        # a failure, so a six-digit code cannot be walked through. Worth
        # pinning: it is also why a wrong code followed immediately by the
        # right one does not enrol.
        self.client.login(username="has-a-password", password="hunter2!x")
        self.client.get(reverse("account:mfa_setup"))
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)

        self.client.post(reverse("account:mfa_setup"), {"token": "000000"})
        self.client.post(reverse("account:mfa_setup"), {"token": code_for(device)})

        self.assertFalse(
            TOTPDevice.objects.filter(user=self.user, confirmed=True).exists()
        )

    def test_enrolling_hands_over_recovery_codes_once(self):
        self.client.login(username="has-a-password", password="hunter2!x")
        self.client.get(reverse("account:mfa_setup"))
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)

        response = self.client.post(
            reverse("account:mfa_setup"), {"token": code_for(device)}
        )

        codes = StaticDevice.objects.get(user=self.user).token_set.count()
        self.assertEqual(codes, mfa.RECOVERY_CODE_COUNT)
        body = response.content.decode()
        self.assertIn("Save these recovery codes", body)

    def test_once_enrolled_the_site_opens(self):
        self.client.login(username="has-a-password", password="hunter2!x")
        self.client.get(reverse("account:mfa_setup"))
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)
        self.client.post(reverse("account:mfa_setup"), {"token": code_for(device)})

        self.assertEqual(self.client.get("/course/list/").status_code, 200)

    def test_enrolment_returns_to_the_original_destination(self):
        destination = "/course/list/?view=mine"
        self.client.login(username="has-a-password", password="hunter2!x")
        held = self.client.get(destination)
        self.assertIn("next=%2Fcourse%2Flist%2F%3Fview%3Dmine", held["Location"])
        self.client.get(held["Location"])
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)

        response = self.client.post(
            reverse("account:mfa_setup"),
            {"token": code_for(device), "next": destination},
        )

        self.assertContains(response, f'href="{destination.replace("&", "&amp;")}"')

    def test_enrolment_never_returns_to_an_external_destination(self):
        self.client.login(username="has-a-password", password="hunter2!x")
        self.client.get(reverse("account:mfa_setup"))
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)

        response = self.client.post(
            reverse("account:mfa_setup"),
            {
                "token": code_for(device),
                "next": "https://attacker.example/collect",
            },
        )

        self.assertContains(response, 'href="/"')


@override_settings(MFA_ENFORCED=True)
class VerificationAtEachSignInTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("enrolled", password="hunter2!x")
        self.device = TOTPDevice.objects.create(
            user=self.user, name="authenticator", confirmed=True
        )
        self.client = Client()

    def test_a_password_alone_gets_you_the_token_page_and_nothing_else(self):
        self.client.login(username="enrolled", password="hunter2!x")

        response = self.client.get("/course/list/")

        self.assertRedirects(
            response,
            f"{reverse('account:mfa_verify')}?next=%2Fcourse%2Flist%2F",
            fetch_redirect_response=False,
        )

    def test_the_right_code_opens_the_site(self):
        self.client.login(username="enrolled", password="hunter2!x")

        self.client.post(
            reverse("account:mfa_verify"), {"token": code_for(self.device)}
        )

        self.assertEqual(self.client.get("/course/list/").status_code, 200)

    def test_login_and_mfa_return_to_the_original_destination(self):
        destination = "/course/list/?view=mine"
        login_response = self.client.post(
            reverse("account:login"),
            {
                "stid": "enrolled",
                "password": "hunter2!x",
                "next": destination,
            },
        )
        self.assertRedirects(
            login_response,
            destination,
            fetch_redirect_response=False,
        )
        held = self.client.get(destination)
        self.assertIn("next=%2Fcourse%2Flist%2F%3Fview%3Dmine", held["Location"])

        verified = self.client.post(
            reverse("account:mfa_verify"),
            {"token": code_for(self.device), "next": destination},
        )

        self.assertRedirects(
            verified,
            destination,
            fetch_redirect_response=False,
        )

    def test_failed_password_keeps_the_original_destination(self):
        destination = "/course/list/?view=mine"

        response = self.client.post(
            reverse("account:login"),
            {
                "stid": "enrolled",
                "password": "wrong-password",
                "next": destination,
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('account:login')}?success=false&next="
            "%2Fcourse%2Flist%2F%3Fview%3Dmine",
            fetch_redirect_response=False,
        )

    def test_mfa_never_redirects_to_an_external_destination(self):
        self.client.login(username="enrolled", password="hunter2!x")

        response = self.client.post(
            reverse("account:mfa_verify"),
            {
                "token": code_for(self.device),
                "next": "https://attacker.example/collect",
            },
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_recovery_code_keeps_the_destination_through_reenrolment(self):
        destination = "/course/list/?view=mine"
        static = StaticDevice.objects.create(
            user=self.user,
            name=mfa.RECOVERY_DEVICE_NAME,
        )
        static.token_set.create(token="rescue-route")
        self.client.login(username="enrolled", password="hunter2!x")

        response = self.client.post(
            reverse("account:mfa_verify"),
            {"token": "rescue-route", "next": destination},
        )

        self.assertRedirects(
            response,
            f"{reverse('account:mfa_setup')}?next=%2Fcourse%2Flist%2F%3Fview%3Dmine",
            fetch_redirect_response=False,
        )

    def test_a_wrong_code_does_not(self):
        self.client.login(username="enrolled", password="hunter2!x")

        self.client.post(reverse("account:mfa_verify"), {"token": "000000"})

        self.assertRedirects(
            self.client.get("/course/list/"),
            f"{reverse('account:mfa_verify')}?next=%2Fcourse%2Flist%2F",
            fetch_redirect_response=False,
        )

    def test_a_recovery_code_works_once(self):
        static = StaticDevice.objects.create(user=self.user, name="recovery codes")
        static.token_set.create(token="rescue-me")
        self.client.login(username="enrolled", password="hunter2!x")

        self.client.post(reverse("account:mfa_verify"), {"token": "rescue-me"})
        self.assertEqual(static.token_set.count(), 0)

        # And it is spent: a second session cannot reuse it.
        other = Client()
        other.login(username="enrolled", password="hunter2!x")
        other.post(reverse("account:mfa_verify"), {"token": "rescue-me"})
        self.assertRedirects(
            other.get("/course/list/"),
            f"{reverse('account:mfa_verify')}?next=%2Fcourse%2Flist%2F",
            fetch_redirect_response=False,
        )

    def test_verification_uses_the_transactional_django_otp_api(self):
        from unittest.mock import patch

        self.client.login(username="enrolled", password="hunter2!x")
        token = code_for(self.device)
        with patch("peer_account.mfa.verify_token", wraps=mfa.verify_token) as verify:
            self.client.post(
                reverse("account:mfa_verify"), {"token": token}
            )

        verify.assert_called_once()
        called_user, called_device_id, called_token = verify.call_args.args
        self.assertEqual(called_user.pk, self.user.pk)
        self.assertEqual(called_device_id, self.device.persistent_id)
        self.assertEqual(called_token, token)

    def test_a_wrong_code_preserves_the_safe_destination(self):
        self.client.login(username="enrolled", password="hunter2!x")

        response = self.client.post(
            reverse("account:mfa_verify"),
            {"token": "000000", "next": "/course/list/"},
        )

        self.assertContains(
            response,
            '<input type="hidden" name="next" value="/course/list/">',
            html=True,
        )


@override_settings(MFA_ENFORCED=True)
class WaysRoundItTests(TestCase):
    """The bypasses. Each of these was reachable before it was closed."""

    def setUp(self):
        self.user = User.objects.create_user("enrolled", password="hunter2!x")
        self.device = TOTPDevice.objects.create(
            user=self.user, name="authenticator", confirmed=True
        )

    def test_a_password_cannot_enrol_a_second_authenticator(self):
        # Otherwise the password alone is enough: add your own device and
        # walk past the one already on the account.
        client = Client()
        client.login(username="enrolled", password="hunter2!x")

        response = client.get(reverse("account:mfa_setup"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("account:mfa_verify")))
        self.assertFalse(
            TOTPDevice.objects.filter(user=self.user, confirmed=False).exists()
        )

    def test_the_admin_is_not_a_way_in_without_the_token(self):
        # /admin/login/ is a second login form wired straight to
        # ModelBackend, so a factor demanded only by the site's own login
        # view would be walked round by going there.
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        client = Client()
        client.login(username="enrolled", password="hunter2!x")

        response = client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account:mfa_verify"), response["Location"])

    def test_the_admin_opens_once_the_token_is_given(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        client = Client()
        client.login(username="enrolled", password="hunter2!x")
        client.post(reverse("account:mfa_verify"), {"token": code_for(self.device)})

        self.assertEqual(client.get("/admin/").status_code, 200)

    def test_an_unanchored_admin_route_no_longer_matches_anywhere(self):
        # re_path(r"admin/") is matched with re.search, so /course/admin/
        # reached the admin as well.
        self.assertEqual(Client().get("/course/admin/").status_code, 404)

    def test_a_password_reset_does_not_hand_out_a_session(self):
        # Resetting by email gives an attacker the password. It must not
        # also give them the second factor, or the mailbox is the only one.
        from django.contrib.auth.views import PasswordResetConfirmView

        self.assertFalse(PasswordResetConfirmView.post_reset_login)


@override_settings(MFA_ENFORCED=True)
class FactorManagementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "replace-me",
            password="hunter2!x",
            email="replace-me@example.com",
        )
        self.old = TOTPDevice.objects.create(
            user=self.user, name=mfa.DEVICE_NAME, confirmed=True
        )
        self.client = Client()
        self.client.login(username=self.user.username, password="hunter2!x")

    def authorize_change(self):
        return self.client.post(
            reverse("account:mfa_verify"),
            {
                "token": code_for(self.old),
                "reauth": "change",
                "next": reverse("account:mfa_setup"),
            },
        )

    def test_an_existing_verified_session_must_step_up_again(self):
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = self.old.persistent_id
        session.save()

        response = self.client.get(reverse("account:mfa_setup"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("reauth=change", response["Location"])
        self.assertIn("next=%2Faccount%2Fmfa%2Fsetup%2F", response["Location"])

    def test_recent_explicit_verification_opens_factor_management(self):
        response = self.authorize_change()

        self.assertRedirects(
            response, reverse("account:mfa_setup"), fetch_redirect_response=False
        )
        self.assertTrue(mfa.factor_change_is_authorized(response.wsgi_request))
        self.assertEqual(
            self.client.get(reverse("account:mfa_setup")).status_code, 200
        )

    def test_factor_management_proof_expires_after_five_minutes(self):
        self.authorize_change()
        session = self.client.session
        authorization = session[mfa.CHANGE_AUTHORIZED_SESSION_KEY]
        authorization["at"] -= mfa.CHANGE_AUTHORIZATION_SECONDS + 1
        session[mfa.CHANGE_AUTHORIZED_SESSION_KEY] = authorization
        session.save()

        response = self.client.get(reverse("account:mfa_setup"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("reauth=change", response["Location"])
        self.assertFalse(
            TOTPDevice.objects.filter(user=self.user, confirmed=False).exists()
        )

    def test_factor_management_proof_dies_with_the_factor_that_granted_it(self):
        self.authorize_change()
        self.old.delete()
        TOTPDevice.objects.create(
            user=self.user,
            name=mfa.DEVICE_NAME,
            confirmed=True,
        )

        response = self.client.get(reverse("account:mfa_setup"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("reauth=change", response["Location"])
        self.assertFalse(
            TOTPDevice.objects.filter(user=self.user, confirmed=False).exists()
        )

    def test_replacement_revokes_the_old_device_and_selects_the_new_one(self):
        self.authorize_change()
        self.client.get(reverse("account:mfa_setup"))
        replacement = TOTPDevice.objects.get(user=self.user, confirmed=False)

        response = self.client.post(
            reverse("account:mfa_setup"), {"token": code_for(replacement)}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(TOTPDevice.objects.filter(user=self.user, confirmed=True)),
            [replacement],
        )
        self.assertEqual(mfa.confirmed_device(self.user), replacement)
        self.assertFalse(mfa.factor_change_is_authorized(response.wsgi_request))

    def test_replacement_notifies_the_account_email(self):
        self.authorize_change()
        self.client.get(reverse("account:mfa_setup"))
        replacement = TOTPDevice.objects.get(user=self.user, confirmed=False)

        self.client.post(
            reverse("account:mfa_setup"), {"token": code_for(replacement)}
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("replaced", mail.outbox[0].body)

    def test_replacement_revokes_old_recovery_codes(self):
        old_recovery = StaticDevice.objects.create(
            user=self.user,
            name=mfa.RECOVERY_DEVICE_NAME,
        )
        old_recovery.token_set.create(token="old-code")
        self.authorize_change()
        self.client.get(reverse("account:mfa_setup"))
        replacement = TOTPDevice.objects.get(user=self.user, confirmed=False)

        self.client.post(
            reverse("account:mfa_setup"), {"token": code_for(replacement)}
        )

        recovery = StaticDevice.objects.get(
            user=self.user, name=mfa.RECOVERY_DEVICE_NAME
        )
        self.assertNotEqual(recovery.pk, old_recovery.pk)
        self.assertFalse(
            recovery.token_set.filter(token="old-code").exists()
        )
        self.assertEqual(recovery.token_set.count(), mfa.RECOVERY_CODE_COUNT)

    def test_management_link_is_visible_on_the_owners_edit_page(self):
        self.authorize_change()

        body = self.client.get(
            reverse("account:edit", kwargs={"uid": self.user.pk})
        ).content.decode()

        self.assertIn("Manage two-factor authentication", body)


@override_settings(MFA_ENFORCED=True)
class MFARateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_username_rotation_does_not_lock_out_another_account(self):
        User.objects.create_user("victim", password="correct-password")
        attacker = Client()
        for number in range(11):
            attacker.post(
                reverse("account:login"),
                {"stid": f"guess-{number}", "password": "wrong"},
            )

        response = Client().post(
            reverse("account:login"),
            {"stid": "victim", "password": "correct-password"},
        )

        self.assertNotEqual(response.status_code, 429)

    def test_distributed_guesses_against_one_account_are_limited(self):
        for number in range(26):
            response = Client(REMOTE_ADDR=f"192.0.2.{number}").post(
                reverse("account:login"),
                {"stid": "one-target", "password": "wrong"},
            )

        self.assertEqual(response.status_code, 429)

    def test_a_lecture_hall_does_not_exhaust_the_broad_ip_limit(self):
        request = RequestFactory().post(
            reverse("account:login"), REMOTE_ADDR="192.0.2.200"
        )

        for number in range(150):
            self.assertFalse(
                _too_many_attempts(request, "login", f"student-{number}")
            )

        for number in range(150, 1001):
            blocked = _too_many_attempts(
                request, "login", f"rotating-guess-{number}"
            )

        self.assertTrue(blocked)


@override_settings(MFA_ENFORCED=True)
class MFAWebSocketGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("socket-user", password="hunter2!x")
        self.device = TOTPDevice.objects.create(
            user=self.user, name=mfa.DEVICE_NAME, confirmed=True
        )

    def test_password_only_session_is_rejected(self):
        self.assertFalse(websocket_mfa_is_verified(self.user.pk, None))

    def test_otp_verified_session_is_accepted(self):
        self.assertTrue(
            websocket_mfa_is_verified(self.user.pk, self.device.persistent_id)
        )

    def test_another_users_device_is_rejected(self):
        other = User.objects.create_user("other-socket-user", password="hunter2!x")
        self.assertFalse(
            websocket_mfa_is_verified(other.pk, self.device.persistent_id)
        )

    def test_passwordless_cwl_account_is_exempt(self):
        cwl = User.objects.create_user("cwl-socket-user")
        cwl.set_unusable_password()
        cwl.save(update_fields=["password"])
        self.assertTrue(websocket_mfa_is_verified(cwl.pk, None))

    def test_inactive_account_is_rejected_even_if_passwordless(self):
        inactive = User.objects.create_user("inactive-socket-user")
        inactive.set_unusable_password()
        inactive.is_active = False
        inactive.save(update_fields=["password", "is_active"])

        self.assertFalse(websocket_mfa_is_verified(inactive.pk, None))


@override_settings(MFA_ENFORCED=True)
class MFAWebSocketMiddlewareTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("socket-stack", password="hunter2!x")
        self.device = TOTPDevice.objects.create(
            user=self.user, name=mfa.DEVICE_NAME, confirmed=True
        )

    def run_middleware(self, persistent_device_id, allowed):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from peer_account.middleware import RequireSecondFactorASGIMiddleware

        application_calls = []
        sent = []

        async def accepted_application(scope, receive, send):
            application_calls.append(scope)

        scope = {"user": self.user, "session": {}}
        if persistent_device_id:
            scope["session"][DEVICE_ID_SESSION_KEY] = persistent_device_id

        async def scenario():
            async def receive():
                return {"type": "websocket.connect"}

            async def send(event):
                sent.append(event)

            verifier = AsyncMock(return_value=allowed)
            with patch(
                "peer_account.middleware._websocket_mfa_is_verified", verifier
            ):
                await RequireSecondFactorASGIMiddleware(accepted_application)(
                    scope, receive, send
                )
            if persistent_device_id:
                verifier.assert_awaited_once_with(
                    self.user.pk, persistent_device_id
                )
            else:
                verifier.assert_awaited_once_with(self.user.pk, None)

        asyncio.run(scenario())
        return application_calls, sent

    def test_password_only_socket_is_closed_by_the_asgi_middleware(self):
        application_calls, sent = self.run_middleware(None, allowed=False)

        self.assertEqual(application_calls, [])
        self.assertEqual(sent, [{"type": "websocket.close", "code": 4403}])

    def test_otp_verified_socket_reaches_the_application(self):
        application_calls, sent = self.run_middleware(
            self.device.persistent_id, allowed=True
        )

        self.assertEqual(len(application_calls), 1)
        self.assertEqual(sent, [])

    @override_settings(WEBSOCKET_MFA_REVALIDATION_SECONDS=0)
    def test_open_socket_is_closed_when_its_factor_is_revoked(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from peer_account.middleware import RequireSecondFactorASGIMiddleware

        sent = []

        async def sending_application(scope, receive, send):
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.send", "text": "private event"})

        async def scenario():
            async def receive():
                return {"type": "websocket.connect"}

            async def send(event):
                sent.append(event)

            verifier = AsyncMock(side_effect=[True, False])
            scope = {
                "user": self.user,
                "session": {DEVICE_ID_SESSION_KEY: self.device.persistent_id},
            }
            with patch(
                "peer_account.middleware._websocket_mfa_is_verified", verifier
            ):
                await RequireSecondFactorASGIMiddleware(sending_application)(
                    scope, receive, send
                )

        asyncio.run(scenario())

        self.assertEqual(
            sent,
            [
                {"type": "websocket.accept"},
                {"type": "websocket.close", "code": 4403},
            ],
        )


class MFADeviceConstraintTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user("one-device", password="hunter2!x")

    def test_database_rejects_two_confirmed_authenticators(self):
        TOTPDevice.objects.create(user=self.user, confirmed=True)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TOTPDevice.objects.create(user=self.user, confirmed=True)

    def test_database_rejects_two_pending_authenticators(self):
        TOTPDevice.objects.create(user=self.user, confirmed=False)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TOTPDevice.objects.create(user=self.user, confirmed=False)

    def test_database_rejects_two_managed_recovery_devices(self):
        StaticDevice.objects.create(
            user=self.user,
            name=mfa.RECOVERY_DEVICE_NAME,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaticDevice.objects.create(
                    user=self.user,
                    name=mfa.RECOVERY_DEVICE_NAME,
                )

    def test_other_named_static_devices_are_not_constrained(self):
        StaticDevice.objects.create(user=self.user, name="operator device")
        StaticDevice.objects.create(user=self.user, name="operator device")


IN_MEMORY_CHANNELS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@override_settings(
    MFA_ENFORCED=True,
    ALLOWED_HOSTS=["testserver"],
    CHANNEL_LAYERS=IN_MEMORY_CHANNELS,
)
class MFAASGIStackIntegrationTests(TransactionTestCase):
    """Exercise the real cookie/session/auth/MFA/routing stack together."""

    def setUp(self):
        self.user = User.objects.create_user("full-stack", password="hunter2!x")
        self.device = TOTPDevice.objects.create(
            user=self.user,
            name=mfa.DEVICE_NAME,
            confirmed=True,
        )
        self.course = Course.objects.create(
            displayname="MFA Socket Course",
            browsable=True,
            archived=False,
            agora_mode=True,
        )
        CourseMember.objects.create(
            course=self.course,
            user=self.user,
            role="student",
        )
        self.client = Client()
        self.client.login(username=self.user.username, password="hunter2!x")

    def socket(self):
        from mta2.asgi import application

        cookie_name = settings.SESSION_COOKIE_NAME
        cookie_value = self.client.cookies[cookie_name].value
        return WebsocketCommunicator(
            application,
            f"/ws/{self.course.pk}/",
            headers=[
                (b"host", b"testserver"),
                (b"origin", b"http://testserver"),
                (
                    b"cookie",
                    f"{cookie_name}={cookie_value}".encode("ascii"),
                ),
            ],
        )

    def mark_session_verified(self):
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = self.device.persistent_id
        session.save()

    def test_real_asgi_stack_rejects_a_password_only_session(self):
        async def scenario():
            socket = self.socket()
            connected, code = await socket.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4403)

        async_to_sync(scenario)()

    def test_real_asgi_stack_accepts_an_otp_verified_session(self):
        self.mark_session_verified()

        async def scenario():
            socket = self.socket()
            connected, _detail = await socket.connect()
            self.assertTrue(connected)
            await socket.disconnect()

        async_to_sync(scenario)()

    @override_settings(WEBSOCKET_MFA_REVALIDATION_SECONDS=0)
    def test_real_open_socket_closes_after_factor_revocation(self):
        self.mark_session_verified()

        async def scenario():
            socket = self.socket()
            connected, _detail = await socket.connect()
            self.assertTrue(connected)
            await database_sync_to_async(
                TOTPDevice.objects.filter(pk=self.device.pk).delete
            )()
            await get_channel_layer().group_send(
                f"course_{self.course.pk}",
                {
                    "type": "send_message",
                    "key": "count-hands-up",
                    "send_auth_id": 0,
                },
            )
            output = await socket.receive_output(timeout=1)
            self.assertEqual(output["type"], "websocket.close")
            self.assertEqual(output["code"], 4403)

        async_to_sync(scenario)()

    @override_settings(WEBSOCKET_MFA_REVALIDATION_SECONDS=0)
    def test_real_open_socket_closes_after_its_session_is_logged_out(self):
        from django.contrib.sessions.models import Session

        self.mark_session_verified()
        session_key = self.client.session.session_key

        async def scenario():
            socket = self.socket()
            connected, _detail = await socket.connect()
            self.assertTrue(connected)
            await database_sync_to_async(
                Session.objects.filter(session_key=session_key).delete
            )()
            await get_channel_layer().group_send(
                f"course_{self.course.pk}",
                {
                    "type": "send_message",
                    "key": "count-hands-up",
                    "send_auth_id": 0,
                },
            )
            output = await socket.receive_output(timeout=1)
            self.assertEqual(output["type"], "websocket.close")
            self.assertEqual(output["code"], 4403)

        async_to_sync(scenario)()


@override_settings(MFA_ENFORCED=True)
class MFALogoutControlTests(TestCase):
    def test_verification_page_uses_a_csrf_protected_post_logout_form(self):
        user = User.objects.create_user("logout-user", password="hunter2!x")
        TOTPDevice.objects.create(user=user, confirmed=True)
        client = Client()
        client.login(username=user.username, password="hunter2!x")

        body = client.get(reverse("account:mfa_verify")).content.decode()

        self.assertIn(f'action="{reverse("account:logout")}"', body)
        self.assertIn('method="post"', body)
        response = client.post(reverse("account:logout"))
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", client.session)


@override_settings(MFA_ENFORCED=True)
class WhoIsExemptTests(TestCase):
    def test_a_cwl_account_is_never_asked(self):
        # _resolve_saml_user calls set_unusable_password on every account it
        # binds, and UBC's sign-in already carries a second factor.
        user = User.objects.create_user("cwl-person")
        user.set_unusable_password()
        user.save()

        self.assertFalse(mfa.needs_enrolment(user))
        self.assertFalse(mfa.needs_verification(user))

    def test_a_demonstration_guest_is_never_asked(self):
        guest = User.objects.create_user("guest-123456-001")
        guest.set_unusable_password()
        guest.save()

        self.assertFalse(mfa.needs_enrolment(guest))

    def test_a_cwl_account_with_an_old_totp_device_is_still_exempt(self):
        user = User.objects.create_user("former-password-account")
        user.set_unusable_password()
        user.save(update_fields=["password"])
        TOTPDevice.objects.create(user=user, confirmed=True)
        client = Client()
        client.force_login(user)

        response = client.get(reverse("account:mfa_setup"))

        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_the_switch_turns_the_demand_off_without_losing_devices(self):
        user = User.objects.create_user("enrolled2", password="hunter2!x")
        TOTPDevice.objects.create(user=user, name="authenticator", confirmed=True)

        with override_settings(MFA_ENFORCED=False):
            self.assertFalse(mfa.needs_verification(user))

        self.assertTrue(TOTPDevice.objects.filter(user=user, confirmed=True).exists())
        self.assertTrue(mfa.needs_verification(user))


class ShippedDefaultTests(TestCase):
    """The suite runs with the demand off. Production must not.

    mta2.test_runner switches MFA_ENFORCED off so that thirty tests which
    sign in with a password are not all about two-factor authentication.
    That is exactly the shape of mistake -- a harness disagreeing with
    production about the setting under test -- that this pins.
    """

    def test_the_shipped_default_is_on(self):
        from pathlib import Path

        settings_py = (
            Path(__file__).resolve().parent.parent / "mta2" / "settings.py"
        ).read_text()

        self.assertIn('MFA_ENFORCED = env_bool("MFA_ENFORCED", True)', settings_py)

    def test_the_env_template_does_not_ship_it_off(self):
        from pathlib import Path

        template = (
            Path(__file__).resolve().parent.parent / "deploy" / "generate_env.py"
        ).read_text()

        self.assertNotIn("MFA_ENFORCED=false", template.lower())

    def test_the_asgi_stack_installs_the_mfa_gate(self):
        from pathlib import Path

        asgi_py = (
            Path(__file__).resolve().parent.parent / "mta2" / "asgi.py"
        ).read_text()

        self.assertIn("RequireSecondFactorASGIMiddleware(", asgi_py)

    def test_production_attempt_counters_use_the_shared_redis_cache(self):
        from pathlib import Path

        settings_py = (
            Path(__file__).resolve().parent.parent / "mta2" / "settings.py"
        ).read_text()

        self.assertIn(
            '"BACKEND": "django.core.cache.backends.redis.RedisCache"',
            settings_py,
        )


@override_settings(MFA_ENFORCED=True)
class ManualEntryKeyTests(TestCase):
    """The fallback for someone who cannot scan the QR.

    TOTPDevice.key is hex; every authenticator app wants base32. Showing the
    hex meant the one person who could not scan was the one person left with
    an authenticator that never produced a matching code.
    """

    def setUp(self):
        self.user = User.objects.create_user("cannot-scan", password="hunter2!x")
        self.device = TOTPDevice.objects.create(
            user=self.user, name="authenticator", confirmed=False
        )

    def test_the_key_shown_is_the_one_in_the_qr_code(self):
        import base64
        from urllib.parse import parse_qs, urlparse

        shown = mfa.manual_entry_key(self.device).replace(" ", "")
        in_the_qr = parse_qs(urlparse(self.device.config_url).query)["secret"][0]

        self.assertEqual(shown, in_the_qr)

    def test_it_is_base32_not_hex(self):
        shown = mfa.manual_entry_key(self.device).replace(" ", "")

        self.assertNotEqual(shown.lower(), self.device.key.lower())
        self.assertTrue(set(shown) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"))

    def test_the_page_shows_it_grouped_for_typing(self):
        client = Client()
        client.login(username="cannot-scan", password="hunter2!x")

        body = client.get(reverse("account:mfa_setup")).content.decode()

        self.assertIn(mfa.manual_entry_key(self.device), body)
