"""Making, showing and joining a demonstration class."""

import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import lecture_guests, sandbox, sandbox_guard
from .base import CourseBase
from .models import Course, CourseMember


def _enabled():
    if not getattr(settings, "GUEST_SESSIONS_ENABLED", False):
        raise Http404("Demonstration classes are switched off on this server.")


def _owned_sandbox(request, cid):
    """The sandbox this person runs, or 404.

    Membership is the check: a sandbox is not listed anywhere and nobody is
    enrolled in it except its owner and whoever typed its code.
    """
    course = get_object_or_404(Course, pk=cid, is_sandbox=True)
    member = CourseBase.get_course_member(request.user, course.id)
    if member is None or not member.is_instructor():
        raise Http404("No such demonstration class.")
    return course, member


def _clean_name(raw):
    return re.sub(r"\s+", " ", (raw or "").strip())[:40]


def _login_guest(request, user, course):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    # Marked so the account cannot wander: see `sandbox_guard`.
    request.session["sandbox_guest_course"] = course.id
    request.session["course_id"] = course.id


class SandboxViews:
    @staticmethod
    def start(request):
        """Offer a demonstration class, and make one on request.

        Signed out, this is a door on the sign-in page: the class is owned by a
        temporary instructor that is deleted with it. Signed in with a real
        account, the class is owned by that account, which it leaves alone.
        """
        _enabled()
        sandbox.expire_due()
        if sandbox_guard.is_guest(request.user):
            # A guest is confined to one class; a new class needs a new guest.
            owned = request.user.memberships.filter(
                is_sandbox_guest=True, role="instructor",
                course__is_sandbox=True,
            ).first()
            if owned and request.method != "POST":
                return HttpResponseRedirect(
                    reverse("course:sandbox_console", kwargs={"cid": owned.course_id})
                )
            logout(request)
        if not request.user.is_authenticated:
            return SandboxViews._start_as_guest(request)
        mine = [
            member.course
            for member in CourseMember.objects.filter(
                user=request.user, role="instructor", active=True,
                course__is_sandbox=True,
            ).select_related("course")
            if member.course.sandbox_expires_at
            and member.course.sandbox_expires_at > timezone.now()
        ]

        if request.method == "POST":
            if len(mine) >= 3:
                messages.error(
                    request,
                    "You already have three demonstration classes running. "
                    "End one before starting another.",
                )
                return HttpResponseRedirect(reverse("course:sandbox_start"))
            course = sandbox.create(request.user)
            return HttpResponseRedirect(
                reverse("course:sandbox_console", kwargs={"cid": course.id})
            )

        return render(request, "sandbox-start.html", {
            "running": mine,
            "lifetime_hours": int(sandbox.LIFETIME.total_seconds() // 3600),
        })

    @staticmethod
    def _join_lecture(request, lecture, context, display_name):
        """A guest code for a real course's open lecture."""
        course = lecture.course
        if context["member_account"]:
            joined = lecture_guests.join(lecture, user=request.user)
        else:
            if not display_name:
                return render(request, "sandbox-join.html", {
                    **context,
                    "error": "Give a name, so the instructor knows who was called on.",
                })
            joined = lecture_guests.join(lecture, name=display_name)
        if joined is None:
            return render(request, "sandbox-join.html", {
                **context, "error": "This lecture has no room for more guests.",
            })
        member, created = joined
        if created is not None:
            _login_guest(request, created, course)
        else:
            request.session["course_id"] = course.id
        return HttpResponseRedirect("/")

    @staticmethod
    def _start_as_guest(request):
        context = {
            "running": [],
            "guest": True,
            "lifetime_hours": int(sandbox.LIFETIME.total_seconds() // 3600),
        }
        if request.method != "POST":
            return render(request, "sandbox-start.html", context)
        if sandbox.guest_classes_full():
            context["error"] = (
                "Too many demonstration classes are running right now. "
                "Try again later, or sign in to start one."
            )
            return render(request, "sandbox-start.html", context)
        name = _clean_name(request.POST.get("name")) or "Demo instructor"
        first, _, last = name.partition(" ")
        user = User.objects.create_user(
            username=sandbox._unique_username(
                f"guest-host-{timezone.now():%Y%m%d%H%M%S}"
            ),
            first_name=first[:30],
            last_name=last[:30],
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        course = sandbox.create(user, guest_owner=True)
        _login_guest(request, user, course)
        return HttpResponseRedirect(
            reverse("course:sandbox_console", kwargs={"cid": course.id})
        )

    @staticmethod
    @login_required
    def console(request, cid):
        """Everything about one demonstration class, on one screen.

        The instructor's own view and a student's view side by side, so trying
        Agora out does not require a second browser, a second account, or a
        colleague.
        """
        _enabled()
        course, _member = _owned_sandbox(request, cid)
        # Both panes are iframes of the ordinary lecture screen, which reads
        # the selected course from the session. Selecting it here is what makes
        # "/" and "/?as=<id>" show this class.
        request.session["course_id"] = course.id

        students = list(
            CourseMember.objects.filter(course=course, role="student", active=True)
            .select_related("user")
            .order_by("user__first_name", "id")
        )
        # A hand in any list, not only the first.
        for student in students:
            student.any_hand_up = bool(
                student.hand_up or student.hand_up_list_2
                or student.hand_up_list_3 or student.hand_up_list_4
            )
        return render(request, "sandbox-console.html", {
            "course": course,
            "students": students,
            "raised": [student for student in students if student.any_hand_up],
            "session": sandbox.session_of(course),
            "expires_at": course.sandbox_expires_at,
            "join_url": request.build_absolute_uri(reverse("course:sandbox_join")),
        })

    @staticmethod
    @login_required
    @require_POST
    def microphone(request, cid):
        """Switch the classroom microphone on or off for a demonstration class.

        A demo's owner has no course settings page to reach (and should not
        need one), so the one setting worth trying lives on the console.
        """
        _enabled()
        course, _member = _owned_sandbox(request, cid)
        turn_on = request.POST.get("on") == "1"
        Course.objects.filter(pk=course.pk).update(classroom_microphone=turn_on)
        if not turn_on:
            from peer_lecture.microphone import disable_classroom_microphone

            disable_classroom_microphone(course.pk)
        return HttpResponseRedirect(
            reverse("course:sandbox_console", kwargs={"cid": course.id})
        )

    @staticmethod
    @login_required
    @require_POST
    def end(request, cid):
        """Delete a demonstration class now rather than waiting for expiry."""
        _enabled()
        course, _member = _owned_sandbox(request, cid)
        name = course.displayname
        sandbox.remove(course)
        request.session.pop("course_id", None)
        messages.success(request, f"{name} and everything in it has been deleted.")
        return HttpResponseRedirect(reverse("course:sandbox_start"))

    @staticmethod
    def join(request):
        """Join a demonstration class with its code and a name.

        No account, no CWL, no enrolment code read out letter by letter. The
        account this makes belongs to the demonstration class and is deleted
        with it; it is refused everywhere else.
        """
        _enabled()
        sandbox.expire_due()
        code = (request.POST.get("code") or request.GET.get("code") or "").strip()
        display_name = _clean_name(request.POST.get("name"))
        # Someone signed in with a real account joins as themselves; only their
        # membership belongs to the class and is deleted with it.
        member_account = (
            request.user.is_authenticated
            and not sandbox_guard.is_guest(request.user)
        )
        context = {"code": code, "member_account": member_account}

        if request.method != "POST":
            return render(request, "sandbox-join.html", context)

        course = sandbox.for_code(code)
        if course is None:
            lecture = lecture_guests.lecture_for_code(code)
            if lecture is not None:
                return SandboxViews._join_lecture(request, lecture, context, display_name)
        if course is None:
            return render(request, "sandbox-join.html", {
                **context,
                "error": "Nothing is open with that code. Check the digits on "
                         "the screen, or ask for a new code.",
            })
        if member_account:
            CourseMember.objects.get_or_create(
                course=course, user=request.user,
                defaults={"role": "student", "active": True},
            )
            request.session["course_id"] = course.id
            return HttpResponseRedirect("/")
        if not display_name:
            return render(request, "sandbox-join.html", {
                **context,
                "error": "Give a name, so the instructor knows who was called on.",
            })

        joined = CourseMember.objects.filter(
            course=course, is_sandbox_guest=True
        ).count()
        if joined >= 300:
            return render(request, "sandbox-join.html", {
                **context,
                "error": "This demonstration class is full.",
            })

        first, _, last = display_name.partition(" ")
        user = User.objects.create_user(
            username=sandbox._unique_username(
                f"guest-{course.join_code}-{joined:03d}"
            ),
            first_name=first[:30],
            last_name=last[:30],
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        CourseMember.objects.create(
            course=course, user=user, role="student", active=True,
            is_sandbox_guest=True,
        )
        _login_guest(request, user, course)
        return HttpResponseRedirect("/")
