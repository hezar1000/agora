import logging
from collections import defaultdict

logger = logging.getLogger(__name__)
import datetime
from datetime import datetime, date, time, timedelta
import csv
import pytz
from io import StringIO
import json 
import pandas as pd
import io

from django.shortcuts import (
    get_object_or_404,
    HttpResponseRedirect,
    HttpResponse,
    reverse,
    render as djangoRender,
)
from django.contrib import messages
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse, HttpResponseBadRequest
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from django.http import JsonResponse, HttpResponseNotAllowed, HttpResponse
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.timezone import now, make_aware
from peer_home.wrappers import render
from django.views.decorators.http import require_POST
from peer_lecture.models import Lecture, Poll, PollResult, Message
from peer_lecture.microphone import disable_classroom_microphone
from .participation import course_session_participation_grades
from . import selection_policy
from .models import Course, CourseMember, CourseParticipation, CourseSession, HandRaise
from . import course_home
from . import participation_forms
from . import lecture_calendar
from . import sandbox_guard
from . import participation_export
from . import participation_rules
from . import participation_standing
from . import participation_summary
from . import participation_presets
from .participation_forms import (
    MeasureRubricFormSet,
    ParticipationSchemeForm,
)
from .forms import (
    CourseForm,
    CourseSessionForm,
    CourseSessionFormSet,
    LectureFormSet,
)
from .decorators import chosen_course_required
from peer_home.views import HomeViews
from .base import CourseBase, CoursePermissions
from django.core.mail import EmailMessage
from itertools import groupby
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

eventLogger = logging.getLogger("agora.events")


def _safe_csv_cell(value):
    """Prevent spreadsheet programs from evaluating exported user text as formulas."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class _FormulaSafeCSVWriter:
    def __init__(self, target):
        self._writer = csv.writer(target)

    def writerow(self, row):
        return self._writer.writerow([_safe_csv_cell(value) for value in row])

    def writerows(self, rows):
        return self._writer.writerows(
            [_safe_csv_cell(value) for value in row] for row in rows
        )


def _csv_writer(target):
    return _FormulaSafeCSVWriter(target)


class CourseViews:
    @staticmethod
    @login_required
    @require_POST
    def create(request):
        # A guest joined a demonstration class with a six-digit code and a
        # name. OPEN_COURSE_CREATION is on so that a new instructor is not
        # deadlocked out of their first course, which would otherwise let a
        # guest make a real one.
        if sandbox_guard.is_guest(request.user):
            return HttpResponse(sandbox_guard.REFUSAL, status=403)
        can_create = (
            request.user.is_superuser
            or CourseBase.is_instructor_some_course(request.user)
            or settings.OPEN_COURSE_CREATION
        )
        if not can_create:
            return HttpResponse("Course creation requires an instructor invitation.", status=403)
        cname = request.POST.get("cname")

        browsable = False  
        archived = False   
        agora_mode = True 
        on_list = True

        if Course._default_manager.filter(displayname=cname).exists():
            messages.error(
                request,
                "Cannot create course with name %s because there is already a course with this name." % cname,
            )
            return HttpResponseRedirect("/course/list/")

        c = CourseBase.create(
            request.user,
            cname,
            browsable=browsable,
            agora_mode=agora_mode,
            archived=archived,
            on_list=on_list,
        )
        if c is not None:
            messages.success(request, "Successfully created the course %s" % cname)
            messages.warning(request, "A course is NOT browsable when created.")

            CourseBase._enroll(request.user, c, "instructor")
            return HttpResponseRedirect("/course/list/")
        else:
            messages.error(request, "Error occurred creating the course %s" % cname)
            return HttpResponseRedirect("/course/list/")
    



    @staticmethod
    @login_required
    def list(request):
        "View the list of courses that I am associated with"

        render_dict = dict()

        cid = request.session.get("course_id", None)
        if cid is not None:
            course = Course._default_manager.filter(pk=cid).first()
            has_active_membership = (
                course is not None
                and (
                    request.user.is_superuser
                    or request.user.memberships.filter(
                        course=course, active=True
                    ).exists()
                )
            )
            if has_active_membership:
                render_dict["course"] = course
            else:
                # A course can be deleted or a membership can be revoked while
                # the browser still holds the old selection. Treat that
                # selection as expired instead of raising a 404/500 from this
                # recovery page or from the shared template wrapper.
                request.session.pop("course_id", None)
                request.session.pop("course_role", None)

        render_dict["courses"] = Course._default_manager.all().order_by("displayname")
        render_dict["messages"] = get_messages(request)

        courses_as_student = request.user.memberships.filter(role="student", active=True)
        courses_as_ta = request.user.memberships.filter(role="ta", active=True)
        courses_as_instructor = request.user.memberships.filter(
            role="instructor", active=True
        )

        render_dict["courses_as_student"] = courses_as_student
        render_dict["courses_as_ta"] = courses_as_ta
        render_dict["courses_as_instructor"] = courses_as_instructor
        render_dict["has_visible_course"] = (
            (request.user.is_superuser and Course._default_manager.exists())
            or courses_as_student.exists()
            or courses_as_ta.exists()
            or courses_as_instructor.exists()
        )

        render_dict["next"] = request.GET.get("next", "")

        return render(request, "course-list.html", render_dict)

    @staticmethod
    def _participation_forms(course, data=None):
        """The scheme forms, creating a scheme for a course that has none.

        A course without a scheme keeps the behaviour it had before schemes
        existed, so one is only created when an instructor first opens these
        settings -- seeded from the rubric the course already grades by, so
        opening the page changes nothing on its own.
        """
        scheme = getattr(course, "participation_scheme", None)
        if scheme is None:
            scheme = participation_presets.apply_preset(
                course, participation_presets.suggest_preset(course)
            )
            course.refresh_from_db()
        rubrics = scheme.rubrics.order_by("measure")
        return (
            ParticipationSchemeForm(data, instance=scheme, prefix="scheme"),
            MeasureRubricFormSet(data, queryset=rubrics, prefix="rubrics"),
        )

    @staticmethod
    @login_required
    def edit(request, cid):
        course = Course._default_manager.get(id=cid)
        CoursePermissions.require_instructor(request.user, cid)

        render_dict = {}
        # The weekly pattern and the lectures it produced are different things
        # and were previously edited through one formset that only ever showed
        # the pattern. An instructor could describe "Tuesdays at ten" but say
        # nothing about last Tuesday, which is where a name and an exclusion
        # from participation belong.
        templates = course.sessions.filter(is_template=True)
        # Chronological. Newest-first made sense while this listed only
        # lectures that had already run; a term entered in advance reads as a
        # term, from the first lecture to the last.
        lectures = course.sessions.filter(is_template=False).order_by(
            "session_date", "start_time_pst"
        )

        if request.method == "POST":
            formCourse = CourseForm(request.POST, instance=course)
            session_formset = CourseSessionFormSet(
                request.POST, instance=course, queryset=templates
            )
            lecture_formset = LectureFormSet(
                request.POST, queryset=lectures, prefix="lectures"
            )
            scheme_form, rubric_formset = CourseViews._participation_forms(
                course, request.POST
            )
            chosen_preset = request.POST.get("scheme-preset", "")
            if chosen_preset and chosen_preset != participation_forms.KEEP:
                # A starting point replaces the curves wholesale, so the values
                # posted alongside it describe the scheme being replaced and
                # would be written back over the preset that was just applied.
                participation_presets.apply_preset(course, chosen_preset)
                messages.success(
                    request,
                    "Applied "
                    f"{participation_presets.PRESETS[chosen_preset]['label']}.",
                )
                return HttpResponseRedirect(
                    reverse("course:edit", kwargs={"cid": course.id})
                )
            if (
                formCourse.is_valid()
                and session_formset.is_valid()
                and lecture_formset.is_valid()
                and scheme_form.is_valid()
                and rubric_formset.is_valid()
            ):
                for subform in session_formset:
                    subform.instance.is_template = True
                formCourse.save()
                if (
                    "classroom_microphone" in formCourse.changed_data
                    and not formCourse.instance.classroom_microphone
                ):
                    # Close every open microphone socket for this course now,
                    # not at its next lease check.
                    disable_classroom_microphone(course.pk)
                session_formset.save()
                lecture_formset.save()
                scheme_form.save()
                rubric_formset.save()

                messages.success(request, "Course configuration updated.")
                return HttpResponseRedirect(reverse("home:home"))

            messages.error(request, "Please correct the errors below.")
        else:
            formCourse = CourseForm(instance=course)
            session_formset = CourseSessionFormSet(
                queryset=templates, instance=course
            )
            lecture_formset = LectureFormSet(queryset=lectures, prefix="lectures")
            scheme_form, rubric_formset = CourseViews._participation_forms(course)

        render_dict["formCourse"] = formCourse
        render_dict["selection_policy"] = selection_policy.describe(course)
        render_dict["session_formset"] = session_formset
        render_dict["lecture_formset"] = lecture_formset
        render_dict["scheme_form"] = scheme_form
        render_dict["rubric_formset"] = rubric_formset
        render_dict["course"] = course
        render_dict["queue_labels"] = course.queue_labels()
        course.refresh_from_db()
        summary = participation_summary.for_course(course)
        render_dict["participation_summary"] = summary
        # Each measure's share of the grade, carried on the form that sets it,
        # so the page can say "5 of 15 -- a third" beside the box holding "5".
        if summary:
            shares = {entry["measure"]: entry for entry in summary["measures"]}
            for form in rubric_formset:
                entry = shares.get(form.instance.measure)
                form.share = entry["share"] if entry else None
                form.sentences = entry["sentences"] if entry else []
        render_dict["calendar"] = lecture_calendar.build(course)
        render_dict["patterns"] = lecture_calendar.pattern_sentences(list(templates))
        if request.method == "POST":
            render_dict["messages"] = get_messages(request)
        return render(request, "course-edit.html", render_dict)
    @staticmethod
    @login_required
    @chosen_course_required
    def get_color_config(request, cid):
        """
        Returns the color configuration JSON for the specified course.
        """
        CoursePermissions.require_course_member(request.user, cid)
        course = get_object_or_404(Course, pk=cid)
        try:
            color_config = json.loads(course.color_config) if course.color_config else {}
        except json.JSONDecodeError:
            color_config = {}
        return JsonResponse(color_config)
    
    @staticmethod
    @login_required
    def view(request, cid):
        course = get_object_or_404(Course, id=cid)
        CoursePermissions.require_course_member(request.user, cid)
        request.session["course_id"] = course.id

        # This used to compute a colour config, discard it, and redirect to the
        # lecture screen -- so a course had no page of its own, and the roster,
        # the exports it links to, and a student's own standing had no route to
        # them at all.
        member = CourseBase.get_course_member(request.user, course.id)
        return render(
            request,
            "course-home.html",
            course_home.context_for(course, member, request.user),
        )


    @staticmethod
    @login_required
    @require_POST
    def modify(request, cid):
        "Show, hide, or archive a course"
        action = request.POST.get("action", "")
        CoursePermissions.require_instructor(request.user, cid)

        eventLogger.info("Modifying course (%s) with action: %s" % (str(cid), action))

        if action == "show":
            Course._default_manager.filter(id=cid).update(browsable=True)
        elif action == "hide":
            Course._default_manager.filter(id=cid).update(browsable=False)
        elif action == "archive":
            Course._default_manager.filter(id=cid).update(archived=True)
        else:
            messages.error(request, "Operation not supported")

        return HttpResponseRedirect("/course/list/")

    @staticmethod
    @login_required
    def list_users(request, cid):
        CoursePermissions.require_course_staff(request.user, cid)
        course = Course._default_manager.get(id=cid)
        render_dict = {}
        render_dict["course"] = course
        return render(request, "course-list-users.html", render_dict)
    
    @staticmethod
    @login_required
    def user_view(request, cid, uid):
        "View a participant's Agora activity in one authorized course."
        course = get_object_or_404(Course, id=cid)
        coursemember = get_object_or_404(
            CourseMember.objects.select_related("user", "user__profile"),
            course=course,
            user_id=uid,
            active=True,
        )
        viewing_self = request.user.id == coursemember.user_id
        if not viewing_self:
            CoursePermissions.require_course_staff(request.user, cid)
        participations = CourseParticipation.objects.filter(
            participant=coursemember,
            count_in_calculations=True,
        ).select_related("session")
        hand_raises = HandRaise.objects.filter(
            member=coursemember,
        ).select_related("session")
        totals = participations.aggregate(
            points=Sum("participation_points_gained"),
            bonus=Sum("bonus_points"),
        )
        standing = (
            participation_standing.standing_for(course, coursemember)
            if course.show_participation_grade
            else None
        )
        # Courses without the configurable scheme retain their historical
        # per-session grade.  A configured course must not show both systems.
        session_grades = []
        if course.show_participation_grade and standing is None:
            for session in course_session_participation_grades(course):
                result = session["students"].get(coursemember.id)
                if result is None:
                    continue
                session_grades.append(
                    {
                        "label": session["label"],
                        "median": session["median"],
                        "maximum": session["maximum"],
                        **result,
                    }
                )

        return render(
            request,
            "course-user-participation.html",
            {
                "course": course,
                "coursemember": coursemember,
                "participations": participations.order_by("-time_participated")[:200],
                "hand_raises": hand_raises.order_by("-raised_at")[:200],
                "total_points": (totals["points"] or 0) + (totals["bonus"] or 0),
                "times_spoken": hand_raises.filter(
                    resolution=HandRaise.Resolution.SPOKE
                ).count(),
                "record_count": hand_raises.count(),
                "session_grades": session_grades,
                "standing": standing,
                # Staff see where the arithmetic stands today; students see
                # their record, because the arithmetic is provisional all term.
                "is_staff_view": not viewing_self,
            },
        )

    @staticmethod
    @login_required
    def add_user(request, cid):
        "Add the selected user to the course for the role"
        CoursePermissions.require_instructor(request.user, cid)

        if request.method == "POST":
            course = get_object_or_404(Course, pk=cid)
            user_id = request.POST.get("user", 0)
            if user_id == "":
                user_id = 0
            user = get_object_or_404(User, pk=user_id)
            role = request.POST.get("role", "student")
            if role == "":
                role = "student"

            cm = CourseMember._default_manager.filter(course=course, user=user).first()

            eventLogger.info(
                "Course membership added",
                extra={"course_id": course.id, "member_id": user.id, "role": role},
            )

            if cm is not None:
                if cm.active:
                    messages.error(
                        request,
                        "Cannot add user %s to this course. This user is already associated with this course."
                        % user.username,
                    )
                else:
                    cm.active = True
                    cm.role = role
                    cm.save()
            else:
                CourseMember._default_manager.create(course=course, user=user, role=role)

        return HttpResponseRedirect("/course/%s/list_users/" % cid)
    

    

    @staticmethod
    @login_required
    @require_POST
    def remove_user(request, cid, uid):
        "Deactivates user account: he/she can't activate it by themselves"
        CoursePermissions.require_instructor(request.user, cid)

        course = get_object_or_404(Course, pk=cid)
        user = get_object_or_404(User, pk=uid)

        eventLogger.warning(
            "Course membership deactivated",
            extra={"course_id": course.id, "member_id": user.id},
        )

        CourseMember._default_manager.filter(course=course, user=user).update(active=False)
        return HttpResponseRedirect("/course/%s/list_users/" % cid)

    @staticmethod
    @login_required
    def enroll(request):
        "Enroll the user in a course"
        if request.method == "POST":
            client = request.META.get("REMOTE_ADDR", "unknown")
            rate_key = f"course-enroll:{request.user.pk}:{client}"
            try:
                attempt_count = cache.incr(rate_key)
            except ValueError:
                cache.set(rate_key, 1, timeout=300)
                attempt_count = 1
            if attempt_count > 10:
                messages.error(request, "Too many enrollment attempts. Please try again later.")
                return HttpResponseRedirect("/course/list/")
            if sandbox_guard.is_guest(request.user):
                messages.error(request, sandbox_guard.REFUSAL)
                return HttpResponseRedirect(reverse("home:home"))
            code = request.POST.get("coursecode")
            try:
                cm = CourseBase.enroll(request.user, code)
                if cm is not None:
                    messages.success(request, "Successfully enrolled in the course.")
                    eventLogger.info(
                        "Course enrollment succeeded",
                        extra={
                            "course_id": cm.course_id,
                            "member_id": cm.user_id,
                            "role": cm.role,
                        },
                    )
                else:
                    messages.error(request, "Please provide a valid course code.")
            except AssertionError as err:
                messages.error(request, str(err))

        return HttpResponseRedirect("/course/list/")

    @staticmethod
    @login_required
    def export_instructors(request, cid):
        "Export list of instructors for a course"
        CoursePermissions.require_course_staff(request.user, cid)
        course = Course._default_manager.get(id=cid)
        instructors = CourseMember._default_manager.filter(course=course, active=True, role='instructor')

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="Instructor_list_in_course_%s.csv"'
            % (course.displayname)
        )

        writer = _csv_writer(response)
        headers = ["Instructor ID", "First Name", "Last Name", "Email Address"]
        writer.writerow(headers)
        for instructor in instructors:
            if instructor.active:
                row = [
                    instructor.user.username,
                    instructor.user.first_name,
                    instructor.user.last_name,
                    instructor.user.email
                ]
                writer.writerow(row)

        return response

    @staticmethod
    @login_required
    def export_tas(request, cid):
        "Export list of TAs for a course"
        CoursePermissions.require_course_staff(request.user, cid)
        course = Course._default_manager.get(id=cid)
        tas = CourseMember._default_manager.filter(course=course, active=True, role='ta')

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="TA_list_in_course_%s.csv"'
            % (course.displayname)
        )

        writer = _csv_writer(response)
        headers = ["TA ID", "First Name", "Last Name", "Email Address"]
        writer.writerow(headers)
        for ta in tas:
            if ta.active:
                row = [
                    ta.user.username,
                    ta.user.first_name,
                    ta.user.last_name,
                    ta.user.email
                ]
                writer.writerow(row)

        return response

    @staticmethod
    @login_required
    def export_students(request, cid):
        "Export list of students for a course"
        CoursePermissions.require_course_staff(request.user, cid)
        course = Course._default_manager.get(id=cid)
        students = CourseMember._default_manager.filter(course=course, active=True, role='student')

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="Student_list_in_course_%s.csv"'
            % (course.displayname)
        )

        writer = _csv_writer(response)
        headers = ["Student ID", "First Name", "Last Name", "Email Address"]
        writer.writerow(headers)
        for student in students:
            if student.active:
                row = [
                    student.user.username,
                    student.user.first_name,
                    student.user.last_name,
                    student.user.email
                ]
                writer.writerow(row)

        return response

    @staticmethod
    @login_required
    @chosen_course_required
    def export_participation_data_custom(request, cid):
        """One of three participation reports, as a CSV.

        ``download_type`` is ``grades`` (what goes in the gradebook),
        ``lectures`` (hands raised, one column per lecture) or ``full``
        (everything recorded). The old values ``points_only`` and ``all`` still
        resolve, so links already in circulation keep working.
        """
        course = Course.objects.get(id=cid)
        CoursePermissions.require_instructor(request.user, course.id)

        start_date_str = request.GET.get("start_date")
        end_date_str = request.GET.get("end_date")
        start_date = end_date = None
        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                return HttpResponse(
                    "Invalid date format. Must be YYYY-MM-DD.", status=400
                )
            if end_date < start_date:
                return HttpResponse("End date cannot be before start date.", status=400)

        kind = participation_export.resolve_kind(request.GET.get("download_type"))
        lists = [
            name for name in request.GET.get("lists", "").split(",") if name.strip()
        ]
        course_tz = pytz.timezone(course.course_timezone)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="{participation_export.filename(course, kind, course_tz)}"'
        )
        writer = _csv_writer(response)
        writer.writerows(
            participation_export.build(
                course, kind=kind, start_date=start_date, end_date=end_date,
                lists=lists,
            )
        )
        return response

    @login_required
    @chosen_course_required
    @require_POST
    def export_participation_data_email(request, cid):
        """
        Generates and emails a participation data CSV. Can produce two types of reports:
        - 'all' (default): Full report with all metrics per session.
        - 'points_only': Simplified report with only 'Points Gained' per session.
        """
        course = Course.objects.get(id=cid)
        CoursePermissions.require_instructor(request.user, course.id)

        # Only deliver identifiable student data to an active staff address in this course.
        start_date_str = request.POST.get("start_date")
        end_date_str = request.POST.get("end_date")
        recipient_email = request.POST.get("email", "").strip()
        email_type = request.POST.get("email_type", "all")

        if not recipient_email:
            return HttpResponse("No email address provided.", status=400)
        try:
            validate_email(recipient_email)
        except ValidationError:
            return HttpResponse("Invalid email address.", status=400)
        allowed_recipients = set(
            CourseMember.objects.filter(
                course=course,
                active=True,
                role__in=("instructor", "ta"),
            )
            .exclude(user__email="")
            .values_list("user__email", flat=True)
        )
        if request.user.is_superuser and request.user.email:
            allowed_recipients.add(request.user.email)
        allowed_recipients = {address.casefold() for address in allowed_recipients}
        if recipient_email.casefold() not in allowed_recipients:
            return HttpResponse(
                "Exports may only be sent to an active staff address for this course.",
                status=403,
            )
        if email_type not in {"all", "points_only"}:
            return HttpResponse("Invalid export type.", status=400)
        
        # ... (Date parsing remains the same) ...
        start_date, end_date = None, None
        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                if end_date < start_date:
                    return HttpResponse("End date cannot be before start date.", status=400)
            except ValueError:
                return HttpResponse("Invalid date format. Must be YYYY-MM-DD.", status=400)

        course_tz = pytz.timezone(course.course_timezone)
        export_time_filename = now().astimezone(course_tz).strftime("%Y-%m-%d_%H-%M-%S")
        
        # --- Create CSV in memory ---
        csv_buffer = io.StringIO()
        writer = _csv_writer(csv_buffer)
        
        writer.writerow([f"Exported on: {now().astimezone(course_tz).strftime('%Y-%m-%d %H:%M:%S %Z')}"])
        writer.writerow([])

        all_sessions = CourseSession.objects.filter(course=course, is_template=False)
        if start_date and end_date:
            all_sessions = all_sessions.filter(session_date__gte=start_date, session_date__lte=end_date)
        all_sessions = all_sessions.order_by("id")
        session_list = list(all_sessions)
        
        students = CourseMember.objects.filter(course_id=cid, role='student', active=True).order_by("user__last_name", "user__first_name")

        # --- Optimization: Pre-fetch participation data ---
        # Fetch all participations for the relevant students and sessions in one go
        all_participations = CourseParticipation.objects.filter(
            participant__in=students,
            session__in=session_list,
            count_in_calculations=True
        )

        # Group participations by (student_id, session_id)
        participation_map = defaultdict(list)
        for cp in all_participations:
            participation_map[(cp.participant_id, cp.session_id)].append(cp)

        # --- Generate Headers (Conditional) ---
        headers = ["Student name", "Student ID"]
        for sess in session_list:
            session_label = f"Session on {sess.session_date.strftime('%Y-%m-%d')}"
            if sess.start_time_pst and sess.end_time_pst:
                session_label += f" ({sess.start_time_pst.strftime('%H:%M')} - {sess.end_time_pst.strftime('%H:%M')})"
            
            if email_type == "all":
                selected_lists = [
                    item
                    for item in request.POST.get("lists", "green,blue,red,yellow").split(",")
                    if item in {"green", "blue", "red", "yellow"}
                ]
                headers.extend([
                    f"{session_label} - Hand Raises",
                    f"{session_label} - Times Spoken",
                    f"{session_label} - Bonus Points"
                ])
                for lst in selected_lists:
                    headers.append(f"{session_label} - {lst.strip()} Spoken")
            
            headers.append(f"{session_label} - Points Gained")
        writer.writerow(headers)

        # --- Generate Rows (Conditional) ---
        for student in students:
            row = [student.get_user_fullname(), student.get_user_id()]
            for sess in session_list:
                # Use the pre-fetched map instead of querying the DB
                cparts_in_session = participation_map.get((student.id, sess.id), [])
                
                total_points = sum(cp.participation_points_gained + (cp.bonus_points or 0) for cp in cparts_in_session)
                
                if email_type == "all":
                    session_times_spoken = sum(1 for cp in cparts_in_session if cp.spoke_upon_participation)
                    session_hand_raises = max(
                        0,
                        sum((cp.participation_points_gained or 0) for cp in cparts_in_session)
                        - (session_times_spoken
                           * participation_rules.for_course(course).speak_bonus),
                    )
                    session_bonus_points = sum((cp.bonus_points or 0) for cp in cparts_in_session)
                    
                    row.extend([session_hand_raises, session_times_spoken, session_bonus_points])
                    
                    color_spoke_counts = {"green": 0, "blue": 0, "red": 0, "yellow": 0}
                    color_map = {1: "green", 2: "blue", 3: "red", 4: "yellow"}
                    for cp in cparts_in_session:
                        if cp.spoke_upon_participation:
                            color = color_map.get(cp.participation_list)
                            if color:
                                color_spoke_counts[color] += 1
                    
                    for lst in selected_lists:
                        spoke_count = color_spoke_counts.get(lst.strip(), 0)
                        row.append(str(spoke_count) if spoke_count > 0 else "--")
                
                row.append(str(total_points))
            writer.writerow(row)
            
        # --- Send Email ---
        csv_data = csv_buffer.getvalue()
        csv_buffer.close()

        filename_prefix = "Full_Participation" if email_type == "all" else "Points_Only_Participation"
        filename = f"{filename_prefix}_{course.displayname}_{export_time_filename}.csv"
        
        try:
            email_message = EmailMessage(
                subject=f"Participation Data Export: {course.displayname}",
                body="Please find the requested participation data attached.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient_email]
            )
            email_message.attach(filename, csv_data, "text/csv")
            email_message.send()
        except Exception:
            eventLogger.exception(
                "Participation export delivery failed",
                extra={"course_id": course.id, "requester_id": request.user.id},
            )
            return HttpResponse("Error sending email. Please contact support.", status=500)

        return HttpResponse("Email sent successfully!")


    @staticmethod
    @login_required
    @chosen_course_required
    def export_poll_participation(request, cid): # Renamed from export_poll_participation_data
        poll_id = request.GET.get('poll_id', False)
        date = request.GET.get('date', False)
        course = Course._default_manager.get(id=cid)
        CoursePermissions.require_instructor(request.user, course.id)

        response = HttpResponse(content_type="text/csv")
        df_dict = None

        if poll_id:
            response["Content-Disposition"] = (
                'attachment; filename="Poll-Participation-Count-Poll-ID-%s-%s (%s).csv"'
                % (poll_id, course.displayname, timezone.now().strftime("%F-%X"))
            )
            df_dict = {
                "Student ID": [],
                "Student Name": [],
                "Poll Title": [],
                "Correct Answer(s)": [], # Renamed Header
                "Poll Start Time": [],
                "Poll End Time": [],
                "Student Answer": [],
                "Time Answered": [],
            }
            students = CourseMember.objects.filter(course_id=cid, role='student', active=True)
            poll = get_object_or_404(Poll, pk=poll_id, course=course)
            if not poll.dont_save_answer:
                df_dict["Student Answer"] = []

                

            for student in students:
                student_id = student.user_id
                student_name = student.get_user_fullname()
                poll_results_student = PollResult.objects.filter(poll_id=poll_id, auth_user_id=student_id)

                if not poll_results_student.exists():
                    continue

                poll_result = poll_results_student.first()
                poll_title = poll.title
                correct_answers = poll.answer
                if isinstance(correct_answers, list):
                    poll_answer_str = ", ".join(correct_answers)
                else:
                    poll_answer_str = correct_answers
                
                # Convert times to course timezone
                course_tz = pytz.timezone(course.course_timezone) if course.course_timezone else None
                
                def format_time_for_csv(dt):
                    """Convert datetime to course timezone and format for CSV"""
                    if dt is None:
                        return ""
                    if course_tz:
                        local_time = timezone.localtime(dt, course_tz)
                        return local_time.strftime("%Y-%m-%d %H:%M:%S")
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                
                poll_start_time = format_time_for_csv(poll.start_time)
                poll_end_time = format_time_for_csv(poll.end_time)
                time_answered = format_time_for_csv(poll_result.time)
                
                student_answer = poll_result.answer
                if isinstance(student_answer, list):
                    student_answer = ", ".join(student_answer)
                else:
                    student_answer = str(student_answer)

                df_dict["Student ID"].append(student.get_user_id())
                df_dict["Student Name"].append(student_name)
                df_dict["Poll Title"].append(poll_title)
                df_dict["Correct Answer(s)"].append(poll_answer_str) # Use the formatted string
                df_dict["Poll Start Time"].append(poll_start_time)
                df_dict["Poll End Time"].append(poll_end_time)
                df_dict["Student Answer"].append(student_answer)
                df_dict["Time Answered"].append(time_answered)

        else:
            # This 'else' block handles the case for exporting multiple polls over a date range
            response["Content-Disposition"] = (
                'attachment; filename="Poll-Participation-Count-%s (%s).csv"'
                % (course.displayname, timezone.now().strftime("%F-%X"))
            )
            students = CourseMember.objects.filter(course_id=cid, role='student', active=True)
            lectures = (
                Lecture.objects
                    .filter(course_id=cid, start_time__startswith=date)
                    .order_by('start_time')
                if date
                else Lecture.objects.filter(course_id=cid).order_by('start_time')
            )
            polls = Poll.objects.filter(lecture__in=lectures).order_by('lecture')

            df_dict = {
                "Student ID": [],
                "Student Name": [],
                "Lecture ID": [],
                "Poll ID": [],
                "Poll Title": [],
                "Correct Answer(s)": [], # Renamed Header
                "Poll Start Time": [],
                "Poll End Time": [],
                "Student Answer": [],
                "Time Answered": [],
            }

            for student in students:
                student_id = student.user_id
                student_name = student.get_user_fullname()
                poll_results_student = PollResult.objects.filter(poll__in=polls, auth_user_id=student_id).order_by('poll')
                polls_student = Poll.objects.filter(poll_id__in=poll_results_student.values('poll_id')).order_by('lecture')

                if not poll_results_student.exists():
                    continue

                for poll in polls_student:
                    poll_id_val = poll.pk
                    poll_title = poll.title

                    # --- START: FIX FOR POLL EXPORT (APPLIED IN THE SECOND CASE) ---
                    correct_answers = poll.answer
                    if isinstance(correct_answers, list):
                        poll_answer_str = ", ".join(correct_answers)
                    else:
                        poll_answer_str = correct_answers
                    # --- END: FIX FOR POLL EXPORT ---
                    
                    # Convert times to course timezone
                    course_tz = pytz.timezone(course.course_timezone) if course.course_timezone else None
                    
                    def format_time_for_csv(dt):
                        """Convert datetime to course timezone and format for CSV"""
                        if dt is None:
                            return ""
                        if course_tz:
                            local_time = timezone.localtime(dt, course_tz)
                            return local_time.strftime("%Y-%m-%d %H:%M:%S")
                        return dt.strftime("%Y-%m-%d %H:%M:%S")
                    
                    poll_start_time = format_time_for_csv(poll.start_time)
                    poll_end_time = format_time_for_csv(poll.end_time)

                    poll_results_student_poll = poll_results_student.filter(poll=poll).order_by('poll')
                    for poll_result in poll_results_student_poll:
                        time_answered = format_time_for_csv(poll_result.time)
                        lecture_id = poll.lecture.pk
                        student_answer = poll_result.answer
                        if isinstance(student_answer, list):
                            student_answer = ", ".join(student_answer)
                        else:
                            student_answer = str(student_answer)

                        df_dict["Student ID"].append(student.get_user_id())
                        df_dict["Student Name"].append(student_name)
                        df_dict["Lecture ID"].append(lecture_id)
                        df_dict["Poll ID"].append(poll_id_val)
                        df_dict["Poll Title"].append(poll_title)
                        df_dict["Correct Answer(s)"].append(poll_answer_str) # Use the formatted string
                        df_dict["Poll Start Time"].append(poll_start_time)
                        df_dict["Poll End Time"].append(poll_end_time)
                        df_dict["Student Answer"].append(student_answer)
                        df_dict["Time Answered"].append(time_answered)

        df = pd.DataFrame(df_dict).map(_safe_csv_cell)
        df.to_csv(response, index=False)
        return response

    @staticmethod
    @login_required
    @chosen_course_required
    def export_messages(request, cid):
        date = request.GET.get('date', False)
        course = Course._default_manager.get(id=cid)
        CoursePermissions.require_instructor(request.user, course.id)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="Messages-%s (%s).csv"'
            % (course.displayname, timezone.now().strftime("%F-%X"))
        )

        df_dict = {
            "Message ID": [],
            "Lecture ID": [],
            "Student ID": [],
            "Message": [],
            "Message Time": [],
            "Reply": [],
            "Blocked": [],
            "Hidden": [],
            "Broadcasted": [],
        }

        if date:
            msgs = Message.objects.filter(lecture__course_id=cid, time__startswith=date).order_by('lecture')
        else:
            lectures = Lecture.objects.filter(course_id=cid).order_by('start_time')
            msgs = Message.objects.filter(lecture__in=lectures).order_by('lecture')

        for m in msgs:
            df_dict["Message ID"].append(m.pk)
            df_dict["Lecture ID"].append(m.lecture.pk)
            df_dict["Student ID"].append(m.auth_user_id)
            df_dict["Message"].append(m.message)
            df_dict["Message Time"].append(m.time)
            df_dict["Reply"].append(m.reply_message)
            df_dict["Blocked"].append(m.blocked)
            df_dict["Hidden"].append(m.hidden)
            df_dict["Broadcasted"].append(m.broadcast)

        df = pd.DataFrame(df_dict).map(_safe_csv_cell)
        df.to_csv(response, index=False)
        return response
    
    @staticmethod
    @login_required
    @require_POST
    def cancel_next_session(request, cid):
        CoursePermissions.require_instructor(request.user, cid)
        course = get_object_or_404(Course, pk=cid)
        next_session = HomeViews.get_next_template_session(course)
        if not next_session:
            return JsonResponse({"status": "error", "message": "No session found"}, status=404)
        
        next_session.cancel_next_session = True
        next_session.save()
        
        return JsonResponse({"status": "success"})
    
    @staticmethod
    @login_required
    @require_POST
    def update_session_cancel(request, cid):
        CoursePermissions.require_instructor(request.user, cid)
        try:
            data = json.loads(request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)
        session = get_object_or_404(
            CourseSession,
            id=data.get("session_id"),
            course_id=cid,
            is_template=True,
        )
        cancel_value = data.get("cancel_next_session")
        if not isinstance(cancel_value, bool):
            return JsonResponse({"status": "error", "message": "Invalid cancellation value"}, status=400)
        session.cancel_next_session = cancel_value
        session.save(update_fields=["cancel_next_session"])
        return JsonResponse({"status": "success"})
    
    @staticmethod
    @login_required
    @require_POST
    def delete_session(request, cid):
        CoursePermissions.require_instructor(request.user, cid)
        try:
            data = json.loads(request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)
        session = get_object_or_404(
            CourseSession,
            id=data.get("session_id"),
            course_id=cid,
            is_template=True,
        )
        session.delete()
        return JsonResponse({"status": "success"})
    
    
    @staticmethod
    @login_required
    @require_POST
    def uncancel_next_session(request, cid):
        CoursePermissions.require_instructor(request.user, cid)
        course = get_object_or_404(Course, pk=cid)
        next_session = HomeViews.get_next_template_session(course)
        if not next_session:
            return JsonResponse({"status": "error", "message": "No session found"}, status=404)
        
        next_session.cancel_next_session = False
        next_session.save()
        
        return JsonResponse({"status": "success"})
    
    @staticmethod
    @login_required
    @chosen_course_required
    def rename_color(request, cid):
        """
        AJAX endpoint: Rename a given color in the course's color_config (JSON).
        Expects POST with fields:
        - color: one of "green","blue","red","yellow"
        - new_name: the new label
        """
        CoursePermissions.require_instructor(request.user, cid)
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        color = request.POST.get("color", "").strip()
        new_name = request.POST.get("new_name", "").strip()

        if color not in ["green", "blue", "red", "yellow"]:
            return JsonResponse({"error": "invalid color key"}, status=400)

        if not new_name:
            return JsonResponse({"error": "Name cannot be empty."}, status=400)

        if len(new_name) > 15:
            return JsonResponse({"error": "New name cannot exceed 15 characters."}, status=400)
        if any(character in new_name for character in "<>&") or any(
            ord(character) < 32 for character in new_name
        ):
            return JsonResponse({"error": "Name contains unsupported characters."}, status=400)

        course = get_object_or_404(Course, pk=cid)
        try:
            color_config = json.loads(course.color_config) if course.color_config else {}
        except json.JSONDecodeError:
            color_config = {}

        color_config[color] = new_name
        course.color_config = json.dumps(color_config)
        course.save()
        channel_layer = get_channel_layer()
        return JsonResponse({"status": "ok", "color": color, "new_name": new_name})
    
    @staticmethod
    @login_required
    @require_POST
    def set_lecture_skipped(request, cid):
        """Say there is, or is not, a lecture on one date.

        The weekly pattern creates a lecture on every matching day, holidays
        included, and the only way to stop one was to tick "cancel the next
        session" on the morning it would have appeared. This records the
        decision against the date itself, whenever the instructor makes it.
        """
        CoursePermissions.require_instructor(request.user, cid)
        course = get_object_or_404(Course, pk=cid)
        try:
            payload = json.loads(request.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        try:
            day = datetime.strptime(str(payload.get("date", "")), "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({"error": "Expected date as YYYY-MM-DD"}, status=400)
        skipped = payload.get("skipped")
        if not isinstance(skipped, bool):
            return JsonResponse({"error": "Expected skipped true or false"}, status=400)

        session = CourseSession.objects.filter(
            course=course, is_template=False, session_date=day
        ).order_by("id").first()

        if session is None:
            if not skipped:
                return JsonResponse({"status": "ok", "state": "no-lecture-row"})
            # No lecture exists yet on a day the pattern covers. Recording the
            # skip as a row is what stops the pattern from creating one, since
            # it only ever fills an empty slot.
            template = course.sessions.filter(is_template=True).order_by("id").first()
            start = getattr(template, "start_time_pst", None) or time(0, 0)
            end = getattr(template, "end_time_pst", None) or time(23, 59)
            session = CourseSession.objects.create(
                course=course, is_template=False, session_date=day,
                start_time_pst=start, end_time_pst=end,
                cancelled=True, counts_for_participation=False,
            )
            return JsonResponse({"status": "ok", "state": "cancelled",
                                 "session_id": session.id})

        if session.hand_raises.exists() and skipped:
            return JsonResponse(
                {"error": "Hands were raised in this lecture. Uncheck "
                          "\u201ccounts toward the grade\u201d instead, which "
                          "keeps its records."},
                status=409,
            )

        session.cancelled = skipped
        fields = ["cancelled"]
        if skipped:
            session.counts_for_participation = False
            fields.append("counts_for_participation")
        session.save(update_fields=fields)
        return JsonResponse({"status": "ok",
                             "state": "cancelled" if skipped else "lecture",
                             "session_id": session.id})

    @staticmethod
    @login_required
    @require_POST
    def update_session_date(request, cid):
        """
        Updates (or creates) a CourseSession for the given course with the provided session_date.
        Expects a POST request with a 'session_date' field in "YYYY-MM-DD" format.
        """
        CoursePermissions.require_instructor(request.user, cid)
        course = get_object_or_404(Course, pk=cid)
        session_date_str = request.POST.get("session_date", "").strip()
        if not session_date_str:
            return JsonResponse({"error": "Missing session_date"}, status=400)
        
        try:
            session_date = datetime.strptime(session_date_str, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)
        
        course_tz = pytz.timezone(course.course_timezone)
        local_now = timezone.localtime(timezone.now(), course_tz)
        # "The open lecture" is the newest unended one that has already begun.
        # Sessions are ended only once their window passes, so where a term's
        # schedule was entered in advance every later lecture is unended too,
        # and the newest by id is the last one of the term -- whose date this
        # would silently rewrite to today.
        open_session = CourseSession.objects.filter(
            course=course,
            is_template=False,
            ended=False,
            session_date__lte=local_now.date(),
        ).order_by("-id").first()
        if open_session:
            open_session.session_date = session_date
            open_session.save(update_fields=["session_date"])
        else:
            end_time = local_now + (course.default_session_duration or timedelta(hours=1))
            CourseSession.objects.create(
                course=course,
                session_date=session_date,
                start_time_pst=local_now.time(),
                end_time_pst=end_time.time(),
                is_template=False,
            )
        return JsonResponse({"status": "success", "session_date": session_date_str})
    

    @staticmethod
    @login_required
    @chosen_course_required
    def hide_list(request, cid):
        """
        POST: Toggle the hidden state for a color in the color_config.
        Fields:
        color: e.g. "green"
        hidden: "true" or "false"
        """
        CoursePermissions.require_instructor(request.user, cid)
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        color = request.POST.get("color", "").strip()
        hidden_str = request.POST.get("hidden", "").strip()
        if color not in ["green", "blue", "red", "yellow"]:
            return JsonResponse({"error": "invalid color"}, status=400)

        hidden_bool = (hidden_str.lower() == "true")

        course = get_object_or_404(Course, pk=cid)
        try:
            color_config = json.loads(course.color_config) if course.color_config else {}
        except json.JSONDecodeError:
            color_config = {}
        key = f"hidden_{color}"
        color_config[key] = hidden_bool

        course.color_config = json.dumps(color_config)
        course.save()

        return JsonResponse({"status": "ok", "color": color, "hidden": hidden_bool})
