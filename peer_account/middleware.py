"""Hold a password account at the door until it has a second factor.

Enforcement lives in middleware rather than in the login view because a
session can be created without passing through ``AuthViews.user_login``:
Django's admin has its own login form, and a session that predates enrolment
being switched on is already signed in. A gate at the view covers the front
door and leaves the side ones open.

What it does NOT do is log anybody out. It redirects every request to the
enrolment page until a factor exists, which keeps the account reachable --
signing people out instead would make enrolling impossible.
"""

from importlib import import_module
from time import monotonic
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
    load_backend,
)
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.models import Device

from channels.db import database_sync_to_async

from . import mfa

#: Paths reachable while enrolment is outstanding. Everything else redirects.
#: Deliberately short: the enrolment flow itself, the way back out, and the
#: static files the enrolment page is made of.
def _open_paths():
    return {
        reverse("account:mfa_setup"),
        reverse("account:mfa_verify"),
        reverse("account:logout"),
        reverse("account:login"),
    }


def _is_exempt(request):
    path = request.path
    if path in _open_paths():
        return True
    # Static and media are served by nginx in production, but not in tests
    # or in a development server, and a login page without its stylesheet is
    # a bad first impression of a security feature.
    for prefix in (settings.STATIC_URL, getattr(settings, "MEDIA_URL", None)):
        if prefix and path.startswith(prefix):
            return True
    # The admin's own login and logout must stay reachable, or an
    # administrator whose session predates this cannot get to the enrolment
    # page from inside the admin at all.
    if path.startswith("/admin/login") or path.startswith("/admin/logout"):
        return True
    # Health checks answer to the load balancer, not to a person.
    if path.startswith("/health/"):
        return True
    # The SAML endpoints must work for accounts that will never need any of
    # this; a CWL sign-in that lands on the enrolment page is a bug.
    if path.startswith("/sso/"):
        return True
    return False


class RequireSecondFactorMiddleware:
    """Redirect password accounts to enrolment, or to the token page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not _is_exempt(request):
            if mfa.needs_enrolment(user):
                target = reverse("account:mfa_setup")
                return redirect(
                    f"{target}?{urlencode({'next': request.get_full_path()})}"
                )
            if mfa.needs_verification(user):
                target = reverse("account:mfa_verify")
                return redirect(
                    f"{target}?{urlencode({'next': request.get_full_path()})}"
                )
        return self.get_response(request)


def websocket_mfa_is_verified(user_id, persistent_device_id):
    """Apply the same password-account rule to a WebSocket session."""
    User = get_user_model()
    try:
        user = User.objects.only("password", "is_active").get(pk=user_id)
    except User.DoesNotExist:
        return False
    if not user.is_active:
        return False
    if not mfa.enforced() or not user.has_usable_password():
        return True
    if not persistent_device_id:
        return False
    device = Device.from_persistent_id(persistent_device_id)
    return bool(
        device is not None
        and device.user_id == user.pk
        and device.confirmed
    )


async def _websocket_mfa_is_verified(user_id, persistent_device_id):
    return await database_sync_to_async(websocket_mfa_is_verified)(
        user_id, persistent_device_id
    )


def websocket_session_mfa_is_verified(session_key):
    """Reload a socket's session and revalidate ordinary auth plus MFA."""
    if not session_key:
        return False

    SessionStore = import_module(settings.SESSION_ENGINE).SessionStore
    session = SessionStore(session_key=session_key).load()
    try:
        user_id = get_user_model()._meta.pk.to_python(session[SESSION_KEY])
        backend_path = session[BACKEND_SESSION_KEY]
    except (KeyError, TypeError, ValueError):
        return False
    if backend_path not in settings.AUTHENTICATION_BACKENDS:
        return False

    user = load_backend(backend_path).get_user(user_id)
    if user is None or not user.is_active:
        return False
    if hasattr(user, "get_session_auth_hash"):
        session_hash = session.get(HASH_SESSION_KEY)
        current_hash = user.get_session_auth_hash()
        hash_is_valid = bool(
            session_hash
            and (
                constant_time_compare(session_hash, current_hash)
                or any(
                    constant_time_compare(session_hash, fallback_hash)
                    for fallback_hash in user.get_session_auth_fallback_hash()
                )
            )
        )
        if not hash_is_valid:
            return False

    return websocket_mfa_is_verified(
        user.pk,
        session.get(DEVICE_ID_SESSION_KEY),
    )


async def _websocket_session_mfa_is_verified(session_key):
    return await database_sync_to_async(websocket_session_mfa_is_verified)(
        session_key
    )


class RequireSecondFactorASGIMiddleware:
    """Reject password-authenticated sockets until their OTP is verified.

    Channels' AuthMiddlewareStack establishes the ordinary Django user and
    session before this wrapper runs. It does not know about django-otp's
    device marker, so the HTTP gate has to be mirrored explicitly here.
    """

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        user = scope.get("user")
        if user is not None and user.is_authenticated:
            session = scope.get("session")
            persistent_id = (
                session.get(DEVICE_ID_SESSION_KEY) if session is not None else None
            )
            allowed = await _websocket_mfa_is_verified(user.pk, persistent_id)
            if not allowed:
                await send({"type": "websocket.close", "code": 4403})
                return

            # A socket may stay open long after its HTTP session was first
            # checked. Revalidate before bounded server events so replacing or
            # deleting the factor also revokes sockets authenticated by it.
            # The consumer independently checks course membership on the same
            # cadence; keeping this check here covers every future consumer.
            try:
                interval = max(
                    0,
                    float(
                        getattr(
                            settings,
                            "WEBSOCKET_MFA_REVALIDATION_SECONDS",
                            5,
                        )
                    ),
                )
            except (TypeError, ValueError):
                interval = 5
            checked_at = monotonic()
            revoked = False
            session_key = getattr(session, "session_key", None)

            async def checked_send(message):
                nonlocal checked_at, revoked
                if revoked:
                    return
                if (
                    message.get("type") == "websocket.send"
                    and monotonic() - checked_at >= interval
                ):
                    if session_key:
                        still_allowed = await _websocket_session_mfa_is_verified(
                            session_key
                        )
                    else:
                        # Keeps the middleware independently composable in
                        # tests/custom stacks that provide a mapping rather
                        # than Django's SessionStore.
                        still_allowed = await _websocket_mfa_is_verified(
                            user.pk, persistent_id
                        )
                    if not still_allowed:
                        revoked = True
                        await send({"type": "websocket.close", "code": 4403})
                        return
                    checked_at = monotonic()
                await send(message)

            await self.application(scope, receive, checked_send)
            return

        await self.application(scope, receive, send)
