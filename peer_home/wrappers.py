"""
    Wrappers around normal Django functionality
"""
from django.shortcuts import render as djangoRender
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.urls import reverse

from peer_course.models import Course, display_role
from peer_course.base import CourseBase


def render(request, template_name, context=None, *args, **kwargs):
    """
        Add some context variables that are going to be passed in to the templates
    """
    if context is None:
        context = {}
    if settings.DEBUG == True:
        context["logout_address"] = reverse("account:logout")
    else:
        context["logout_address"] = reverse("account:saml_logout")
    if "course_id" in request.session:
        if "course" not in context:
            context["course"] = get_object_or_404(
                Course, pk=request.session["course_id"]
            )
        if "user_role" not in context:
            context["my_course_member"] = CourseBase.get_course_member(
                request.user, context["course"].id
            )
            superuser = request.user.is_superuser
            if superuser:
                user_role = "superuser"
            else:
                user_role = (
                    CourseBase.get_user_role(request.user, context["course"].id) or ""
                )

            context["user_role"] = user_role
            context["user_role_display"] = display_role(user_role)

            context["is_student"] = user_role == "student"
            context["is_ta"] = user_role == "ta"
            context["is_instructor"] = user_role == "instructor" or superuser

            context["is_staff"] = context["is_ta"] or context["is_instructor"]

    return djangoRender(request, template_name, context, *args, **kwargs)
