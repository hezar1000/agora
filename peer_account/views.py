from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import reverse, get_object_or_404

from django.views.decorators.cache import never_cache
from django.conf import settings

from django.contrib import auth, messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth.decorators import login_required, user_passes_test

import json

from django.shortcuts import render as djangoRender

from peer_home.wrappers import render
from peer_course.base import CoursePermissions, CourseBase
from peer_course.models import CourseMember

from .forms import UserForm, UserEditForm

# Create your views here.


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
    @never_cache
    def user_signup(request):
        """Renders the user signup page"""

        if not getattr(settings, "SIGNUP_ENABLED", True):
            messages.warning(
                request,
                "Signing up has been disabled. Please contact course staff to get an account.",
            )
            return HttpResponseRedirect(reverse(settings.LOGIN_URL))

        render_dict = {"is_login": False}
        u = UserForm(data=request.POST or None)
        render_dict["user_form"] = u
        if u.is_valid():
            # Save a new user object from the form's data.
            new_user = u.save()
            new_user.set_password(u.cleaned_data["password"])
            new_user.save()
            return HttpResponseRedirect(reverse("account:login") + "?success=true;")
        else:
            return djangoRender(request, "user-signup-and-login.html", render_dict)

    @staticmethod
    @never_cache
    def user_login(request):
        """Render the login page"""
        if request.method == "POST":
            uid = request.POST["stid"]
            pwd = request.POST["password"]
            user = authenticate(username=uid, password=pwd)
            if user is not None:
                login(request, user)
                direction = request.GET.get("next", "/")
                if not direction:  # direction is an empty string
                    direction = "/"
                return HttpResponseRedirect(direction)
            else:
                return HttpResponseRedirect(
                    reverse("account:login") + "?success=false;"
                )

        render_dict = dict()
        render_dict["is_login"] = True
        render_dict["is_failure"] = str2bool(request.GET.get("success", None)) is False
        render_dict["is_success"] = str2bool(request.GET.get("success", None)) is True
        render_dict["next"] = request.GET.get("next", "")
        return djangoRender(request, "user-signup-and-login.html", render_dict)

    @staticmethod
    @never_cache
    def saml_login(request):
        """Render the login page requesting users to use CWL login"""
        render_dict = dict()
        render_dict["next"] = request.GET.get("next", "")
        return djangoRender(request, "cwl-login.html", render_dict)

    @staticmethod
    @never_cache
    def user_logout(request):
        """Logout the currect session"""
        logout(request)
        return HttpResponseRedirect("/")

    @staticmethod
    @never_cache
    def saml_logout(request):
        """Logout the currect SAML session and redirect to the IdP"""
        return HttpResponseRedirect(reverse("saml:saml2_auth") + "?slo")

    @staticmethod
    @login_required
    def user_view(request, uid):
        render_dict = {}
        render_dict["user"] = user = get_object_or_404(User, pk=uid)
        if request.user != user and not request.user.is_superuser:
            CoursePermissions.require_instructor_some_course(request.user)
        return render(request, "user-view.html", render_dict)

    @staticmethod
    def get_unenrolled_users(request, cid):
        if request.is_ajax():
            q = request.GET.get("term", "")
            users = User._default_manager.filter(email__icontains=q)
            results = [u for u in users if not AuthBase.has_active_membership(u, cid)]
            data_results = []
            for user in results[:20]:
                user_json = {}
                user_json["id"] = user.id
                user_json["value"] = user.first_name + " " + user.last_name
                user_json["label"] = user.email
                data_results.append(user_json)
            data = json.dumps(data_results)
        else:
            data = "fail"
        mimetype = "application/json"
        return HttpResponse(data, mimetype)

    @staticmethod
    @login_required
    def user_edit(request, uid):
        user = get_object_or_404(User, pk=uid)
        if request.user != user and not request.user.is_superuser:
            CoursePermissions.require_instructor_some_course(request.user)

        form = UserEditForm(
            request.POST or None,
            instance=user,
            is_superuser=request.user.is_superuser
            or CourseBase.is_instructor_some_course(request.user),
        )
        if form.is_valid():
            form.save()
            return HttpResponseRedirect(
                reverse("account:view", kwargs={"uid": user.id})
            )

        render_dict = dict()
        render_dict["user"] = user
        render_dict["EditForm"] = form

        return render(request, "user-edit.html", render_dict)
