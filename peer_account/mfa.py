"""Who needs a second factor, and whether they have one yet.

The rule is one line: **an account you can sign into with a password needs a
second factor.** Everything else follows from that, and nothing else needs to
know about roles, courses or products.

Three populations fall out of it without being special-cased:

* CWL accounts. ``_resolve_saml_user`` calls ``set_unusable_password()`` on
  every account it binds, so they are exempt -- and UBC's own sign-in already
  puts a second factor in front of them.
* Demonstration-class guests. ``sandbox_views.join`` also calls
  ``set_unusable_password()``. They hold nothing worth a factor and they
  expire in three hours.
* Anyone who signs up with a username and a password. They are the reason
  this module exists.

``MFA_ENFORCED`` turns the demand off without deleting anybody's device, so a
broken enrolment page cannot lock every password account out of the site.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY, user_has_device, verify_token
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

#: How many single-use codes a newly enrolled account is given.
RECOVERY_CODE_COUNT = 10

#: The name of the one TOTP device an account is allowed.
DEVICE_NAME = "authenticator"

#: The name of its recovery-code device.
RECOVERY_DEVICE_NAME = "recovery codes"

#: A verified factor authorizes factor management only briefly. The normal
#: OTP session remains valid for the login session; changing credentials is a
#: separate, higher-risk operation and needs recent proof.
CHANGE_AUTHORIZED_SESSION_KEY = "mfa_change_authorized_at"
CHANGE_AUTHORIZATION_SECONDS = 300


def enforced():
    return bool(getattr(settings, "MFA_ENFORCED", True))


def is_password_account(user):
    """Whether this account can be signed into with a password at all."""
    return bool(
        user
        and user.is_authenticated
        and user.has_usable_password()
    )


def confirmed_device(user):
    """The account's confirmed authenticator, or None."""
    if not user or not user.is_authenticated:
        return None
    return TOTPDevice.objects.filter(user=user, confirmed=True).first()


def pending_device(user, create=False):
    """The unconfirmed authenticator being enrolled, optionally creating it.

    Kept unconfirmed until a code from it has been checked, so a device that
    was never successfully scanned cannot lock somebody out.
    """
    if not create:
        return TOTPDevice.objects.filter(user=user, confirmed=False).first()

    # TOTPDevice has no uniqueness constraint limiting an account to one
    # pending device. Lock the user row so simultaneous setup requests cannot
    # create different secrets and leave the browser showing an unusable one.
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        devices = list(
            TOTPDevice.objects.select_for_update()
            .filter(user=user, confirmed=False)
            .order_by("pk")
        )
        if devices:
            device = devices[0]
            TOTPDevice.objects.filter(
                pk__in=[extra.pk for extra in devices[1:]]
            ).delete()
            return device
        return TOTPDevice.objects.create(
            user=user, name=DEVICE_NAME, confirmed=False
        )


def needs_enrolment(user):
    """A password account that has not set up its second factor yet."""
    if not enforced():
        return False
    if not is_password_account(user):
        return False
    return confirmed_device(user) is None


def needs_verification(user):
    """A password account with a factor that has not been used this session."""
    if not enforced():
        return False
    if not is_password_account(user):
        return False
    if confirmed_device(user) is None:
        return False
    # is_verified() is added to request.user by OTPMiddleware. A User loaded
    # straight from the ORM has no such method, and the safe reading of "we
    # cannot tell whether this session passed its factor" is that it did not.
    is_verified = getattr(user, "is_verified", None)
    if not callable(is_verified):
        return True
    return not is_verified()


def _issue_recovery_codes_locked(user, count=RECOVERY_CODE_COUNT):
    """Replace this account's recovery codes and return the new ones.

    Shown once. They are the answer to a lost phone that does not require an
    administrator with a shell on the host.
    """
    StaticDevice.objects.filter(
        user=user, name=RECOVERY_DEVICE_NAME
    ).delete()
    device = StaticDevice.objects.create(
        user=user, name=RECOVERY_DEVICE_NAME, confirmed=True
    )
    codes = []
    for _ in range(count):
        token = StaticToken.random_token()
        device.token_set.create(token=token)
        codes.append(token)
    return codes


def issue_recovery_codes(user, count=RECOVERY_CODE_COUNT):
    """Atomically replace an account's recovery-code set."""
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        return _issue_recovery_codes_locked(user, count)


def verify_and_activate_pending_device(user, persistent_id, token):
    """Verify one pending TOTP device and atomically make it the only one.

    ``django_otp.verify_token`` locks the device while consuming the token.
    The outer transaction keeps that lock while the old authenticator and old
    recovery codes are replaced, so two concurrent confirmations cannot each
    leave behind a different valid factor.
    """
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        device = verify_token(user, persistent_id, token)
        if not isinstance(device, TOTPDevice) or device.confirmed:
            return None

        replacing = TOTPDevice.objects.filter(
            user=user, confirmed=True
        ).exclude(pk=device.pk).exists()
        TOTPDevice.objects.filter(user=user).exclude(pk=device.pk).delete()
        device.name = DEVICE_NAME
        device.confirmed = True
        device.save(update_fields=["name", "confirmed"])
        codes = _issue_recovery_codes_locked(user)
        return device, codes, replacing


def verify_confirmed_totp(user, token):
    """Verify the account's one confirmed authenticator under a row lock."""
    device = confirmed_device(user)
    if device is None:
        return None
    return verify_token(user, device.persistent_id, token)


def verify_recovery_code(user, token):
    """Consume a recovery code transactionally from the managed device."""
    device = StaticDevice.objects.filter(
        user=user,
        name=RECOVERY_DEVICE_NAME,
        confirmed=True,
    ).first()
    if device is None:
        return None
    return verify_token(user, device.persistent_id, token)


def authorize_factor_change(request):
    """Bind recent factor-management proof to the factor that supplied it."""
    request.session[CHANGE_AUTHORIZED_SESSION_KEY] = {
        "at": timezone.now().timestamp(),
        "device": request.session.get(DEVICE_ID_SESSION_KEY),
    }


def factor_change_is_authorized(request):
    """Whether the session's *current* factor was used in the last five minutes.

    Merely retaining a timestamp is not enough. Another session may have
    replaced the authenticator since that proof was supplied, in which case
    the old session must not be able to use its stale authorization to replace
    the new factor in turn.
    """
    value = request.session.get(CHANGE_AUTHORIZED_SESSION_KEY)
    current_device = request.session.get(DEVICE_ID_SESSION_KEY)
    is_verified = getattr(request.user, "is_verified", None)
    if (
        not isinstance(value, dict)
        or not current_device
        or not callable(is_verified)
        or not is_verified()
        or value.get("device") != current_device
    ):
        return False
    try:
        age = timezone.now().timestamp() - float(value.get("at"))
    except (TypeError, ValueError):
        return False
    return 0 <= age <= CHANGE_AUTHORIZATION_SECONDS


def consume_factor_change_authorization(request):
    request.session.pop(CHANGE_AUTHORIZED_SESSION_KEY, None)


def manual_entry_key(device):
    """The secret in the form an authenticator app expects, in typing-sized
    groups.

    ``TOTPDevice.key`` is hex. Every authenticator asks for base32 -- it is
    what the otpauth:// URI in the QR carries -- so showing the hex key
    means the one person who cannot scan the QR is the one person who ends
    up with an authenticator that never matches.
    """
    import base64

    secret = base64.b32encode(device.bin_key).decode("ascii").rstrip("=")
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def has_any_device(user):
    return user_has_device(user, confirmed=True)
