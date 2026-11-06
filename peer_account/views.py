# peer_account/views.py
from django.http import HttpResponseRedirect, HttpResponse, Http404, HttpResponseForbidden, FileResponse
from django.shortcuts import reverse, get_object_or_404
from django.views.decorators.cache import never_cache
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import render as djangoRender
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
import json
import hashlib
import logging
import uuid
import random 
import os
from urllib.parse import urlencode

from peer_home.wrappers import render
from peer_course.base import CoursePermissions, CourseBase
from peer_course.models import CourseMember, Course
from peer_course.models import INSTRUCTOR_ROLES

from .forms import UserForm, UserEditForm, ProfileForm
from .models import Profile
from . import mfa


logger = logging.getLogger(__name__)


def _safe_next(request, candidate, default="/"):
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default


def _rate_key(purpose, dimension, value):
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"auth-rate:{purpose}:{dimension}:{digest}"


def _increment_attempt(key, window):
    if cache.add(key, 1, timeout=window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        # The key can expire between ``add`` and ``incr``. Starting a fresh
        # window is safer than turning that harmless race into a 500.
        cache.set(key, 1, timeout=window)
        return 1


def _too_many_attempts(
    request,
    purpose,
    subject,
    *,
    pair_limit=10,
    subject_limit=25,
    ip_limit=1000,
    window=300,
):
    """Layered shared-cache abuse brake without cross-account lockouts.

    The tight limit is on an account/client pair. Broader account and client
    limits make distributed guessing and username rotation more expensive,
    but are deliberately higher so one person behind a campus NAT cannot lock
    everybody else out after ten requests.
    """
    client = request.META.get("REMOTE_ADDR", "unknown")
    normalized_subject = str(subject).strip().casefold() or "unknown"
    counts = (
        (_increment_attempt(
            _rate_key(purpose, "pair", f"{client}\0{normalized_subject}"), window
        ), pair_limit),
        (_increment_attempt(
            _rate_key(purpose, "subject", normalized_subject), window
        ), subject_limit),
        (_increment_attempt(_rate_key(purpose, "ip", client), window), ip_limit),
    )
    return any(count > limit for count, limit in counts)


def _clear_subject_attempts(request, purpose, subject):
    client = request.META.get("REMOTE_ADDR", "unknown")
    normalized_subject = str(subject).strip().casefold() or "unknown"
    cache.delete_many([
        _rate_key(purpose, "pair", f"{client}\0{normalized_subject}"),
        _rate_key(purpose, "subject", normalized_subject),
    ])


def _resolve_login_name(typed):
    """The username to authenticate as, given what the person typed.

    Exact usernames win. Failing that, an email address or a username that
    differs only in case is accepted when it names exactly one active
    password account -- one, because older data holds case-variant duplicates
    and the guess must never silently pick between them. Anything else is
    handed to ``authenticate`` untouched, so it fails the ordinary way.
    """
    typed = (typed or "").strip()
    if not typed:
        return typed
    if User.objects.filter(username=typed).exists():
        return typed
    if "@" in typed:
        candidates = User.objects.filter(email__iexact=typed, is_active=True)
    else:
        candidates = User.objects.filter(username__iexact=typed, is_active=True)
    names = list(candidates.values_list("username", flat=True)[:2])
    if len(names) == 1:
        return names[0]
    return typed


def _can_manage_user(actor, target):
    if actor.is_superuser or actor == target:
        return True
    target_course_ids = target.memberships.filter(active=True).values("course_id")
    return actor.memberships.filter(
        active=True,
        role="instructor",
        course_id__in=target_course_ids,
    ).exists()

def str2bool(v):
    if v is None:
        return None
    return v.lower() in ("yes", "true", "t", "1")


class AuthBase:
    @staticmethod
    def has_active_membership(user, cid):
        cm = user.memberships.filter(course__id=cid).first()
        if cm and cm.active:
            return True
        return False


class AuthViews:
    @staticmethod
    def about(request):
        return render(request, "about.html")

    @staticmethod
    @never_cache
    def user_signup(request):
        if not getattr(settings, "SIGNUP_ENABLED", True):
            messages.warning(
                request,
                "Signing up has been disabled. Please contact course staff to get an account.",
            )
            return HttpResponseRedirect(reverse(settings.LOGIN_URL))

        render_dict = {
            "is_login": False,
            "next": request.POST.get("next", request.GET.get("next", "")),
        }
        u = UserForm(data=request.POST or None)
        render_dict["user_form"] = u

        if u.is_valid():
            new_user = u.save()
            new_user.set_password(u.cleaned_data["password"])
            new_user.save()
            # Straight into enrolment. A password account cannot reach
            # anything until it has a second factor, so bouncing them to the
            # login page first would only make them type the password again
            # on the way to the same place.
            signed_in = authenticate(
                username=new_user.username,
                password=u.cleaned_data["password"],
            )
            if signed_in is not None:
                login(request, signed_in)
                target = reverse("account:mfa_setup")
                destination = _safe_next(
                    request,
                    request.POST.get("next"),
                    default="",
                )
                if destination:
                    target = f"{target}?{urlencode({'next': destination})}"
                return HttpResponseRedirect(target)
            return HttpResponseRedirect(reverse("account:login") + "?success=true;")
        else:
            return djangoRender(request, "user-signup-and-login.html", render_dict)


    @staticmethod
    @never_cache
    def user_login(request):
        """Render the login page"""
        if request.method == "POST":
            uid = request.POST["stid"]
            if _too_many_attempts(request, "login", uid):
                return HttpResponse(
                    "Too many login attempts. Please try again later.",
                    status=429,
                )
            pwd = request.POST["password"]
            user = authenticate(username=_resolve_login_name(uid), password=pwd)
            if user is not None:
                _clear_subject_attempts(request, "login", uid)
                login(request, user)
                return HttpResponseRedirect(
                    _safe_next(
                        request,
                        request.POST.get("next", request.GET.get("next")),
                    )
                )
            else:
                query = {"success": "false"}
                destination = _safe_next(
                    request,
                    request.POST.get("next", request.GET.get("next")),
                    default="",
                )
                if destination:
                    query["next"] = destination
                return HttpResponseRedirect(
                    f"{reverse('account:login')}?{urlencode(query)}"
                )
        render_dict = {
            "is_login": True,
            "is_failure": str2bool(request.GET.get("success", None)) is False,
            "is_success": str2bool(request.GET.get("success", None)) is True,
            "next": request.GET.get("next", "")
        }
        return djangoRender(request, "user-signup-and-login.html", render_dict)

    

    @staticmethod
    @login_required
    @require_POST
    def update_name(request):
        """Handles a POST that updates the current user's first_name."""
        if request.method == 'POST':
            first_name = request.POST.get('first_name', '').strip()
            user = request.user
            user.first_name = first_name
            user.save()
            return HttpResponseRedirect(reverse("course:enroll"))
        else:
            return HttpResponseRedirect(reverse("course:enroll"))

    @staticmethod
    @never_cache
    @require_POST
    def user_logout(request):
        """Logout the current session after Django's CSRF middleware approves it."""
        if request.session.get("saml_login"):
            # Keep SAML Redirect binding intact, but initiate it only after the
            # local, CSRF-protected POST. The IdP-facing SLS endpoint remains
            # available for signed Redirect responses and requests.
            from mta2.views import custom_saml_logout

            return custom_saml_logout(
                request, request.session.get("saml_provider", "ubc-ca")
            )
        # Sessions from the retired one-time-session buttons, which could
        # outlive this release in a browser that never signed out. Nothing
        # creates these any more; demonstration classes expire on their own.
        if request.session.get("temp", False):
            user = request.user
            temp_role = request.session.get("temp_role")
            temp_course_id = request.session.get("temp_course_id")
            with transaction.atomic():
                if temp_role in INSTRUCTOR_ROLES and temp_course_id is not None:
                    Course.objects.filter(
                        pk=temp_course_id,
                        agora_mode=True,
                        members__user=user,
                        members__role="instructor",
                    ).delete()
                CourseMember.objects.filter(user=user).delete()
                user.delete()

        logout(request)
        request.session.flush()
        return HttpResponseRedirect("/")

    @staticmethod
    @login_required
    def user_view(request, uid):
        """View a user's details (including their profile photo)"""
        render_dict = {}
        render_dict["user"] = user = get_object_or_404(User, pk=uid)
        if not _can_manage_user(request.user, user):
            raise Http404()
        return render(request, "user-view.html", render_dict)

    @staticmethod
    @login_required
    def get_unenrolled_users(request, cid):
        course = get_object_or_404(Course, pk=cid)
        if not request.user.is_superuser and not CourseMember.objects.filter(
            course=course,
            user=request.user,
            active=True,
            role="instructor",
        ).exists():
            raise Http404()
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            q = request.GET.get("term", "")
            users = User._default_manager.filter(email__icontains=q)
            results = [u for u in users if not AuthBase.has_active_membership(u, cid)]
            data_results = []
            for user in results[:20]:
                user_json = {
                    "id": user.id,
                    "value": user.first_name + " " + user.last_name,
                    "label": user.email,
                }
                data_results.append(user_json)
            data = json.dumps(data_results)
        else:
            data = "fail"
        return HttpResponse(data, content_type="application/json")
    
    @staticmethod
    @login_required
    def protected_photo(request, uid):
        profile = get_object_or_404(Profile, user_id=uid)
        if not _can_manage_user(request.user, profile.user):
            raise Http404()
    
        if not profile.photo:
            raise Http404("No photo found.")
        
        # Stream through Django so authorization works with local and remote storage.
        profile.photo.open("rb")
        return FileResponse(
            profile.photo,
            as_attachment=False,
            filename=os.path.basename(profile.photo.name),
        )


    @staticmethod
    @login_required
    def user_edit(request, uid):
        """Edit a user's basic info and profile photo"""
        user = get_object_or_404(User, pk=uid)
        if not _can_manage_user(request.user, user):
            raise Http404()

        # Rendering an edit form is read-only. An unsaved instance gives the
        # ModelForm the same empty state without creating a profile on GET (or
        # on an invalid POST).
        profile = Profile.objects.filter(user=user).first() or Profile(user=user)

        if request.method == 'POST':
            form = UserEditForm(
                request.POST,
                instance=user,
                can_edit_identity=request.user.is_superuser,
            )
            profile_form = ProfileForm(
                request.POST,
                request.FILES,
                instance=profile
            )
            
            if form.is_valid() and profile_form.is_valid():
                form.save()
                profile_form.save()
                return HttpResponseRedirect(reverse("account:view", kwargs={"uid": user.id}))
        else:
            form = UserEditForm(
                instance=user,
                can_edit_identity=request.user.is_superuser,
            )
            profile_form = ProfileForm(instance=profile)

        render_dict = {
            "user": user,
            "EditForm": form,
            "ProfileForm": profile_form,
        }
        return render(request, "user-edit.html", render_dict)
    
    


# ----------------------------------------------------------------------
# Second factor for password accounts. See peer_account.mfa for the rule
# about who needs one; these are the two pages that satisfy it.
# ----------------------------------------------------------------------

import qrcode
import qrcode.image.svg
from django_otp import login as otp_login


def _qr_svg(config_url):
    """The enrolment QR as inline SVG.

    SVG rather than a PNG data URI so it stays crisp when someone holds a
    phone up to a projector, and so nothing has to be written to disk.
    """
    image = qrcode.make(
        config_url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10
    )
    import io

    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


class MFAViews:
    @staticmethod
    @never_cache
    @login_required
    def setup(request):
        """Enrol an authenticator, or replace one after verifying."""
        user = request.user
        requested_destination = request.POST.get(
            "next", request.GET.get("next", "")
        )

        if not mfa.is_password_account(user):
            messages.info(
                request,
                "This account signs in with CWL, which already carries its "
                "own second factor.",
            )
            return HttpResponseRedirect("/")

        if not mfa.enforced() and mfa.confirmed_device(user) is None:
            messages.info(
                request, "Two-factor authentication is currently switched off."
            )
            return HttpResponseRedirect("/")

        confirmed = mfa.confirmed_device(user)

        # Factor management requires a code supplied in the last five minutes,
        # not merely an OTP marker that may have lived in the session for days.
        if confirmed is not None and not mfa.factor_change_is_authorized(request):
            query = urlencode({
                "next": reverse("account:mfa_setup"),
                "reauth": "change",
            })
            return HttpResponseRedirect(
                f"{reverse('account:mfa_verify')}?{query}"
            )

        device = mfa.pending_device(user, create=True)

        if request.method == "POST":
            if _too_many_attempts(request, "mfa-setup", user.pk):
                return HttpResponse(
                    "Too many attempts. Please try again later.", status=429
                )
            token = (request.POST.get("token") or "").strip().replace(" ", "")
            activated = mfa.verify_and_activate_pending_device(
                user, device.persistent_id, token
            )
            if activated is not None:
                device, codes, replacing = activated
                otp_login(request, device)
                mfa.consume_factor_change_authorization(request)
                _clear_subject_attempts(request, "mfa-setup", user.pk)
                logger.info(
                    "MFA authenticator %s for user id %s",
                    "replaced" if replacing else "enabled",
                    user.pk,
                )
                if user.email:
                    try:
                        send_mail(
                            (
                                "Your Agora authenticator was replaced"
                                if replacing
                                else "Two-factor authentication was enabled on Agora"
                            ),
                            (
                                "Your Agora authenticator was replaced and new recovery "
                                "codes were issued. Contact Agora support immediately if "
                                "you did not make this change."
                                if replacing
                                else
                                "Two-factor authentication was enabled on your Agora "
                                "account. Contact Agora support immediately if you did "
                                "not make this change."
                            ),
                            settings.DEFAULT_FROM_EMAIL,
                            [user.email],
                            fail_silently=False,
                        )
                    except Exception:
                        # Factor activation has committed and must not be
                        # rolled back by a mail outage. Leave an actionable,
                        # non-PII diagnostic for operations instead.
                        logger.exception(
                            "Could not send MFA change notice for user id %s",
                            user.pk,
                        )
                return djangoRender(
                    request,
                    "mfa-recovery-codes.html",
                    {
                        "codes": codes,
                        "next": _safe_next(request, requested_destination),
                    },
                )
            messages.error(
                request,
                "That code was not right. Check your phone's clock is set "
                "automatically, then try the next code it shows.",
            )

        return djangoRender(
            request,
            "mfa-setup.html",
            {
                "qr_svg": _qr_svg(device.config_url),
                "secret": mfa.manual_entry_key(device),
                "issuer": getattr(settings, "OTP_TOTP_ISSUER", "Agora"),
                "replacing": mfa.confirmed_device(user) is not None,
                "next": requested_destination,
            },
        )

    @staticmethod
    @never_cache
    @login_required
    def verify(request):
        """Ask for the six digits, or a recovery code."""
        user = request.user
        reauth = (
            request.POST.get("reauth") == "change"
            or request.GET.get("reauth") == "change"
        )

        if mfa.confirmed_device(user) is None:
            return HttpResponseRedirect(reverse("account:mfa_setup"))
        if user.is_verified() and not reauth:
            return HttpResponseRedirect(_safe_next(request, request.GET.get("next")))

        if request.method == "POST":
            if _too_many_attempts(request, "mfa-verify", user.pk):
                return HttpResponse(
                    "Too many attempts. Please try again later.", status=429
                )
            token = (request.POST.get("token") or "").strip().replace(" ", "")

            device = mfa.verify_confirmed_totp(user, token)
            if device is not None:
                otp_login(request, device)
                mfa.authorize_factor_change(request)
                _clear_subject_attempts(request, "mfa-verify", user.pk)
                return HttpResponseRedirect(
                    _safe_next(request, request.POST.get("next"))
                )

            # django-otp consumes the recovery code while holding the device
            # row lock, so two concurrent requests cannot spend it twice.
            static = mfa.verify_recovery_code(user, token)
            if static is not None:
                otp_login(request, static)
                mfa.authorize_factor_change(request)
                _clear_subject_attempts(request, "mfa-verify", user.pk)
                messages.warning(
                    request,
                    "That was a recovery code, and it has now been used "
                    "up. Set up your authenticator again to get a fresh "
                    "set.",
                )
                target = reverse("account:mfa_setup")
                destination = _safe_next(
                    request,
                    request.POST.get("next"),
                    default="",
                )
                if destination and destination != target:
                    target = f"{target}?{urlencode({'next': destination})}"
                return HttpResponseRedirect(target)

            messages.error(request, "That code was not right.")

        return djangoRender(
            request,
            "mfa-verify.html",
            {
                # Keep the destination through a failed POST so a mistyped
                # code does not silently discard the page the user wanted.
                "next": request.POST.get("next", request.GET.get("next", "")),
                "reauth": "change" if reauth else "",
            },
        )



# ----------------------------------------------------------------------
# Changing a password you still know. Reset-by-email is for the one you
# have forgotten; this is the ordinary account-settings path.
# ----------------------------------------------------------------------

from django.contrib.auth import views as auth_views
from django.utils.decorators import method_decorator


@method_decorator(never_cache, name="dispatch")
class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "account/password_change_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_usable_password():
            messages.info(
                request,
                "This account signs in with CWL and has no Agora password to change.",
            )
            return HttpResponseRedirect(reverse("account:view", kwargs={"uid": request.user.id}))
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("account:view", kwargs={"uid": self.request.user.id})

    def form_valid(self, form):
        response = super().form_valid(form)
        logger.info("Password changed for user id %s", self.request.user.pk)
        messages.success(self.request, "Your password has been changed.")
        return response
