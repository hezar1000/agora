"""SAML entry points with minimal logging and immutable identity binding."""

import logging

from django.conf import settings
from django.contrib import auth
from django.contrib.auth import logout as django_logout
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from onelogin.saml2.auth import OneLogin_Saml2_Auth

from peer_account.models import FederatedIdentity, Profile

logger = logging.getLogger(__name__)

SAML_LOGOUT_COOKIE = "saml_logout_req"
DEFAULT_DESTINATION = "/course/list/"


def _provider_settings(provider):
    try:
        return settings.SAML_PROVIDERS[provider]
    except KeyError as exc:
        raise Http404("Unknown identity provider") from exc


def _safe_local_redirect(request, candidate, default=DEFAULT_DESTINATION):
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default


# The identity provider returns the assertion by cross-site POST, and browsers
# do not send SameSite=Lax cookies on those. The session is therefore absent at
# the assertion consumer, taking the in-flight request id with it, and
# rejectUnsolicitedResponsesWithInResponseTo then refuses a response that does
# carry an InResponseTo: sign-in appears to succeed and drops back to login.
#
# Only this correlation value has to cross sites, so it travels in its own
# short-lived cookie rather than relaxing SameSite on the session cookie and
# widening every other request. It is signed, so a tampered value is rejected.
SAML_TRANSACTION_COOKIE = "saml_txn"
SAML_TRANSACTION_MAX_AGE = 300
SAML_TRANSACTION_PATH = "/sso/"


def _store_saml_request_id(response, request_id):
    secure = bool(getattr(settings, "SESSION_COOKIE_SECURE", True))
    # SameSite=None is only honoured on secure cookies. A local HTTP-only
    # development instance keeps Lax; it has no real provider to round-trip.
    response.set_signed_cookie(
        SAML_TRANSACTION_COOKIE,
        request_id or "",
        max_age=SAML_TRANSACTION_MAX_AGE,
        secure=secure,
        httponly=True,
        samesite="None" if secure else "Lax",
        path=SAML_TRANSACTION_PATH,
    )
    return response


def _read_saml_request_id(request):
    try:
        value = request.get_signed_cookie(
            SAML_TRANSACTION_COOKIE, max_age=SAML_TRANSACTION_MAX_AGE
        )
    except Exception:
        return None
    return value or None


def _public_server_port(request):
    forwarded_port = request.META.get("HTTP_X_FORWARDED_PORT")
    if forwarded_port:
        return forwarded_port
    if request.is_secure():
        return "443"
    return request.META.get("SERVER_PORT", "80")


def _build_saml_req(request):
    return {
        "https": "on" if request.is_secure() else "off",
        "http_host": request.get_host(),
        "script_name": request.META.get("PATH_INFO", ""),
        # python3-saml rebuilds this request's URL from http_host and
        # server_port and, under strict mode, requires the assertion's
        # Destination to match it. Behind the proxy SERVER_PORT is Daphne's,
        # not the public one, so falling back to it yields a URL no assertion
        # can match and every sign-in is rejected. request.is_secure() already
        # accounts for the trusted proxy header.
        "server_port": _public_server_port(request),
        "get_data": request.GET.copy(),
        "post_data": request.POST.copy(),
    }


def _saml_auth(request, provider):
    return OneLogin_Saml2_Auth(
        _build_saml_req(request), old_settings=_provider_settings(provider)
    )


@never_cache
@require_POST
def custom_saml_logout(request, provider="ubc-ca"):
    """Initiate SAML logout after a CSRF-protected local POST."""
    if not request.session.get("saml_login"):
        django_logout(request)
        request.session.flush()
        return HttpResponseRedirect("/")

    saml_auth = _saml_auth(request, provider)
    try:
        redirect_url = saml_auth.logout(
            name_id=request.session.get("samlNameId"),
            session_index=request.session.get("samlSessionIndex"),
            return_to=settings.SAML_ENTITY_ID,
        )
        logout_request_id = saml_auth.get_last_request_id()
    except Exception:
        logger.exception("SAML logout initiation failed", extra={"provider": provider})
        redirect_url = "/?error=slo_initiation_failed"
        logout_request_id = None

    django_logout(request)
    request.session.flush()
    response = HttpResponseRedirect(redirect_url)
    if logout_request_id:
        response.set_cookie(
            SAML_LOGOUT_COOKIE,
            logout_request_id,
            max_age=300,
            secure=True,
            httponly=True,
            samesite="Lax",
        )
    return response


@never_cache
@csrf_exempt
@require_http_methods(["GET", "POST"])
def custom_saml_sls(request, provider="ubc-ca"):
    """Validate an IdP logout response/request without logging its contents."""
    saml_auth = _saml_auth(request, provider)
    try:
        target = saml_auth.process_slo(
            request_id=request.COOKIES.get(SAML_LOGOUT_COOKIE),
            delete_session_cb=lambda: django_logout(request),
        )
        errors = saml_auth.get_errors()
        if errors:
            logger.warning("SAML logout validation failed", extra={"provider": provider})
            target = "/?error=slo_failed"
        else:
            target = _safe_local_redirect(request, target, "/")
    except Exception:
        logger.exception("SAML logout response processing failed", extra={"provider": provider})
        target = "/?error=slo_exception"

    response = HttpResponseRedirect(target or "/")
    response.delete_cookie(SAML_LOGOUT_COOKIE)
    return response


@csrf_exempt
@require_http_methods(["GET", "POST"])
def legacy_saml_root(request):
    """Compatibility endpoint retained for the registered UBC IdP URLs."""
    provider = "ubc-ca"
    # Login initiation is an application action, not an IdP callback. Never
    # permit it through this CSRF-exempt compatibility dispatcher, regardless
    # of HTTP method; the named custom_saml_login route is the only entry point.
    if "login" in request.GET:
        return HttpResponse(status=405)
    if request.method == "POST" or "acs" in request.GET:
        return custom_saml_acs(request, provider)
    if "sls" in request.GET:
        return custom_saml_sls(request, provider)
    return HttpResponseRedirect("/")


@never_cache
@require_POST
def custom_saml_login(request, provider):
    saml_auth = _saml_auth(request, provider)
    return_to = _safe_local_redirect(request, request.POST.get("next"))
    redirect_url = saml_auth.login(return_to=return_to)
    return _store_saml_request_id(
        HttpResponseRedirect(redirect_url), saml_auth.get_last_request_id()
    )


def _first_attribute(attributes, key):
    values = attributes.get(key) or []
    return str(values[0]).strip() if values and values[0] is not None else ""


def _free_username(base):
    """A name near ``base`` that nobody holds, or None if the range is used up."""
    for suffix in range(2, 102):
        candidate = f"{base}-{suffix}"
        if not User.objects.filter(username=candidate).exists():
            return candidate
    return None


def _resolve_saml_user(*, issuer, student_number, cwl):
    """Resolve one SSO identity without merging into a password account.

    The CWL is the federated subject, not the SAML NameID. UBC issues a
    transient NameID -- a fresh opaque value per assertion -- so binding on it
    creates a new identity row per sign-in and refuses every returning user on
    their second visit. The CWL is stable and released for students and staff
    alike.

    The student number is optional because UBC releases it for students only.
    Requiring it locks out every instructor and staff member.
    """
    subject = cwl
    with transaction.atomic():
        identity = (
            FederatedIdentity.objects.select_for_update()
            .select_related("user")
            .filter(issuer=issuer, subject=subject)
            .first()
        )
        if identity:
            if not identity.user.is_active:
                raise PermissionError("Federated account is disabled")
            return identity.user, False

        # A student number arrives inside the provider's signed response and
        # cannot be claimed, so adopting an account it names is safe even if
        # that account still carries a password from before SSO.
        existing_user = None
        matched_on_signed_identifier = False
        if student_number:
            existing_user = User.objects.select_for_update().filter(
                username=student_number
            ).first()
            matched_on_signed_identifier = existing_user is not None

        # Staff carry no student number, so the only way to reach an account
        # that predates SSO is a CWL an administrator recorded out of band.
        # Matching on the username instead would let anyone who registered a
        # username equal to somebody's CWL be handed that person's account the
        # moment its password was retired. Every legacy instructor account here
        # carries a password, so that is not a hypothetical ordering.
        if existing_user is None and cwl:
            linked = Profile.objects.select_related("user").filter(cwl=cwl).first()
            if linked is not None:
                existing_user = User.objects.select_for_update().get(
                    pk=linked.user_id
                )

        if (
            existing_user
            and not matched_on_signed_identifier
            and existing_user.has_usable_password()
        ):
            raise PermissionError("Username conflicts with a local password account")
        if existing_user and not existing_user.is_active:
            raise PermissionError("Existing account is disabled")

        user = existing_user
        if user is None:
            # Staff assertions carry no student number, so the CWL is the only
            # stable identifier available to name a new account.
            new_username = student_number or cwl
            if not new_username:
                raise PermissionError("Assertion carries no usable identifier")
            # The provider asserts that this person holds this CWL, which is
            # a stronger claim to the name than a local account that merely
            # registered it. So the new account takes the name and any current
            # holder is renamed out of the way.
            #
            # Renaming is not the same as handing the account over: the
            # displaced account keeps its rows, its password and its identity.
            # It loses only a label. What it does cost is password sign-in
            # under the old name, so the rename is logged at warning level for
            # an administrator to see and pass on.
            occupant = (
                User.objects.select_for_update()
                .filter(username=new_username)
                .first()
            )
            if occupant is not None:
                if FederatedIdentity.objects.filter(user=occupant).exists():
                    # Two federated identities cannot legitimately claim one
                    # name. Something is wrong upstream; do not paper over it.
                    raise PermissionError("Username held by another SSO account")
                displaced = _free_username(new_username)
                if displaced is None:
                    raise PermissionError("Could not allocate a username")
                logger.warning(
                    "Renaming local account %r to %r; %r now names the "
                    "federated account for that CWL. Password sign-in for the "
                    "displaced account needs the new name.",
                    occupant.username,
                    displaced,
                    new_username,
                )
                occupant.username = displaced
                occupant.save(update_fields=["username"])

            try:
                with transaction.atomic():
                    user = User.objects.create_user(username=new_username)
            except IntegrityError:
                # Only reachable if a concurrent assertion for this same person
                # won the name in between. Transient: retrying signs them in.
                raise PermissionError("Username conflict")

        user.set_unusable_password()
        user.save(update_fields=["password"])
        try:
            # Keep the uniqueness race inside a savepoint so the outer transaction
            # remains usable when a concurrent ACS request wins first.
            with transaction.atomic():
                FederatedIdentity.objects.create(
                    user=user,
                    issuer=issuer,
                    subject=subject,
                )
        except IntegrityError:
            identity = FederatedIdentity.objects.select_related("user").get(
                issuer=issuer, subject=subject
            )
            if identity.user_id != user.id:
                raise PermissionError("Federated identity conflict")
            user = identity.user
        return user, True


@never_cache
@csrf_exempt  # SAML HTTP-POST is cross-site; the signed assertion is the protocol control.
@require_POST
def custom_saml_acs(request, provider):
    config = _provider_settings(provider)
    saml_auth = OneLogin_Saml2_Auth(_build_saml_req(request), old_settings=config)
    request_id = _read_saml_request_id(request)
    saml_auth.process_response(request_id=request_id)

    if saml_auth.get_errors() or not saml_auth.is_authenticated():
        # The codes and reason distinguish a destination mismatch, an
        # unexpected InResponseTo, a wrong audience and an unverifiable
        # signature, which need different remedies.
        logger.warning(
            "SAML authentication rejected: %s (%s)",
            ", ".join(saml_auth.get_errors()) or "not authenticated",
            saml_auth.get_last_error_reason() or "no reason reported",
            extra={"provider": provider},
        )
        return HttpResponseRedirect("/?error=saml_authentication_rejected")

    attributes = saml_auth.get_attributes()
    # Retained for single logout only; see _resolve_saml_user on why the
    # NameID cannot serve as an identity key.
    name_id = (saml_auth.get_nameid() or "").strip()
    student_number = _first_attribute(
        attributes, "urn:mace:dir:attribute-def:ubcEduStudentNumber"
    )
    first_name = _first_attribute(attributes, "urn:oid:2.5.4.42")
    last_name = _first_attribute(attributes, "urn:oid:2.5.4.4")
    email = _first_attribute(
        attributes, "urn:oid:0.9.2342.19200300.100.1.3"
    )
    cwl = _first_attribute(attributes, "urn:oid:0.9.2342.19200300.100.1.1")
    issuer = config["idp"]["entityId"]

    # ubcEduStudentNumber is released for students only, so requiring it would
    # refuse every instructor and staff member.
    if not name_id or not cwl:
        logger.warning("SAML assertion missing required identity claims", extra={"provider": provider})
        return HttpResponseRedirect("/?error=auth_failed_missing_identity")

    try:
        user, _ = _resolve_saml_user(
            issuer=issuer,
            student_number=student_number,
            cwl=cwl,
        )
    except PermissionError:
        logger.warning("SAML identity requires administrator resolution", extra={"provider": provider})
        return HttpResponseRedirect("/?error=auth_identity_conflict")

    changed_fields = []
    for field_name, value in (
        ("first_name", first_name),
        ("last_name", last_name),
        ("email", email),
    ):
        if value and getattr(user, field_name) != value:
            setattr(user, field_name, value)
            changed_fields.append(field_name)
    if changed_fields:
        user.save(update_fields=changed_fields)

    auth.login(request, user)
    request.session["saml_login"] = True
    request.session["samlNameId"] = name_id
    request.session["samlSessionIndex"] = saml_auth.get_session_index()
    request.session["saml_provider"] = provider

    relay_state = _safe_local_redirect(request, request.POST.get("RelayState"))
    return HttpResponseRedirect(relay_state)
