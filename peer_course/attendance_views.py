"""Attendance mode, and the page each lecture keeps afterwards.

The lecture screen drives a roll call through the JSON endpoints here. The
lecture page (``lecture``) is where its results live once the lecture is over:
who was in the wrong list and never answered, and who has to be followed up.
"""

import csv

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from peer_home.wrappers import render

from . import attendance
from .base import CourseBase, CoursePermissions
from .sessions import held_lectures
from .views import _safe_csv_cell
from .models import (
    AttendanceCheck,
    AttendanceRecord,
    Course,
    CourseMember,
    CourseParticipation,
    CourseSession,
    HandRaise,
)

Status = AttendanceRecord.Status

#: The order a lecture page lists the people it is asking about: the ones
#: still to settle first, then the ones to follow up.
REVIEW_ORDER = {
    Status.UNREVIEWED: 0,
    Status.NOT_IN_CLASS: 1,
    Status.CONFIRMED: 2,
}


def _wants_json(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _name(member):
    return " ".join(part for part in member.display_name_parts() if part).strip() or (
        member.user.username
    )


def _record_json(record, labels):
    return {
        "id": record.pk,
        "name": _name(record.member),
        "student_id": record.member.get_user_id(),
        "lists": [labels.get(position, str(position)) for position in record.lists_raised],
        "status": record.status,
        "status_label": record.get_status_display(),
    }


def _review_records(check):
    records = list(
        check.records.filter(status__in=tuple(REVIEW_ORDER))
        .select_related("member__user", "member__user__profile")
    )
    records.sort(key=lambda r: (REVIEW_ORDER[r.status], _name(r.member).lower()))
    return records


def _dashboard_url(course, check):
    if check.session_id is None:
        return reverse("course:lectures", args=[course.pk])
    return reverse("course:lecture", args=[course.pk, check.session_id])


def _state(course):
    """Everything the instructor's attendance panel shows. Instructor-only."""
    labels = course.queue_labels()
    visible = attendance.visible_lists(course)
    state = {
        "open": False,
        "lists": [{"id": p, "label": labels[p], "colour": Course.QUEUE_COLOURS[p]} for p in visible],
    }
    check = attendance.open_check(course)
    if check is not None:
        counts = attendance.live_counts(check)
        right = counts.get(check.announced_list, 0)
        state.update({
            "open": True,
            "check": {
                "id": check.pk,
                "announced_list": check.announced_list,
                "announced_label": labels[check.announced_list],
                "announced_colour": Course.QUEUE_COLOURS[check.announced_list],
                "started_at": check.started_at.isoformat(),
                "counts": {str(p): counts.get(p, 0) for p in visible},
                "right_list": right,
                "other_lists": sum(n for p, n in counts.items() if p != check.announced_list),
            },
        })
        return state

    # The roll call most recently closed in this lecture, so the instructor
    # can work through it straight away. Once the lecture moves on, it lives
    # on the lecture page.
    session = attendance._lecture_session(course)
    last = (
        AttendanceCheck.objects.filter(course=course, session=session, closed_at__isnull=False)
        .order_by("-closed_at")
        .first()
        if session is not None
        else None
    )
    if last is not None:
        state["last"] = {
            "id": last.pk,
            "announced_label": labels[last.announced_list],
            "summary": attendance.summary(last),
            "review": [_record_json(r, labels) for r in _review_records(last)],
            "dashboard_url": _dashboard_url(course, last),
        }
    return state


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


@login_required
def state(request, cid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_instructor(request.user, cid)
    return JsonResponse(_state(course))


@login_required
@require_POST
def start(request, cid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_instructor(request.user, cid)
    try:
        attendance.start(course, request.user)
    except attendance.AttendanceError as problem:
        return _error(str(problem))
    return JsonResponse(_state(course))


@login_required
@require_POST
def announce(request, cid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_instructor(request.user, cid)
    check = attendance.open_check(course)
    if check is None:
        return _error("No roll call is open.")
    try:
        attendance.announce(check, int(request.POST.get("list", "")))
    except ValueError:
        return _error("Choose a list.")
    except attendance.AttendanceError as problem:
        return _error(str(problem))
    return JsonResponse(_state(course))


@login_required
@require_POST
def close(request, cid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_instructor(request.user, cid)
    check = attendance.open_check(course)
    if check is not None:
        attendance.close(check, request.user)
    return JsonResponse(_state(course))


def _back_to(request, course, check):
    return HttpResponseRedirect(_dashboard_url(course, check) + f"#roll-call-{check.pk}")


@login_required
@require_POST
def review(request, cid, rid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_course_staff(request.user, cid)
    record = get_object_or_404(
        AttendanceRecord.objects.select_related("roll_call", "member__user"),
        pk=rid,
        roll_call__course=course,
    )
    try:
        attendance.review(record, request.POST.get("status", ""), request.user)
    except attendance.AttendanceError as problem:
        if _wants_json(request):
            return _error(str(problem))
        raise Http404(str(problem))
    if _wants_json(request):
        return JsonResponse(_state(course))
    return _back_to(request, course, record.roll_call)


@login_required
@require_POST
def add_present(request, cid, check_id):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_course_staff(request.user, cid)
    check = get_object_or_404(AttendanceCheck, pk=check_id, course=course)
    try:
        member_id = int(request.POST.get("member", ""))
    except ValueError:
        raise Http404("Choose a student.")
    member = get_object_or_404(CourseMember, pk=member_id, course=course)
    try:
        attendance.mark_present(check, member, request.user)
    except attendance.AttendanceError as problem:
        raise Http404(str(problem))
    return _back_to(request, course, check)


@login_required
@require_POST
def remove_added(request, cid, rid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_course_staff(request.user, cid)
    record = get_object_or_404(AttendanceRecord, pk=rid, roll_call__course=course)
    check = record.roll_call
    try:
        attendance.remove_added(record)
    except attendance.AttendanceError as problem:
        raise Http404(str(problem))
    return _back_to(request, course, check)


def _past_sessions(course):
    today = timezone.localtime().date()
    return (
        held_lectures(CourseSession.objects.filter(
            course=course, is_template=False, cancelled=False, session_date__lte=today
        ))
        .annotate(
            roll_calls=Count("attendance_checks", distinct=True),
            to_follow_up=Count(
                "attendance_checks__records",
                filter=Q(attendance_checks__records__status__in=(Status.NOT_IN_CLASS, Status.UNREVIEWED)),
                distinct=True,
            ),
        )
        .order_by("-session_date", "-start_time_pst")
    )


@login_required
def lectures(request, cid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_course_staff(request.user, cid)
    return render(request, "course-lectures.html", {
        "course": course,
        "sessions": _past_sessions(course),
    })


def _lecture_numbers(course, session):
    """The few figures that describe how a lecture went."""
    enrolled = CourseMember.objects.filter(course=course, role="student", active=True).count()
    # Hands raised for the roll call were an answer, not participation.
    hands = HandRaise.objects.filter(session=session).exclude(
        resolution=HandRaise.Resolution.ATTENDANCE
    )
    raised = hands.aggregate(total=Count("id"), students=Count("member", distinct=True))
    calls = CourseParticipation.objects.filter(
        session=session, spoke_upon_participation=True, real_participation=True
    )
    # Anyone with credit, as the grade counts it. `real_participation` marks
    # only the student chosen; everyone else credited for a call is False.
    credited = CourseParticipation.objects.filter(
        Q(participation_points_gained__gt=0) | Q(bonus_points__gt=0),
        session=session, count_in_calculations=True,
        participant__role="student",
    ).aggregate(n=Count("participant", distinct=True))["n"]
    return {
        "enrolled": enrolled,
        "hands_raised": raised["total"],
        "students_raised": raised["students"],
        "called_on": calls.count(),
        "students_credited": credited,
    }


@login_required
def lecture(request, cid, sid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_course_staff(request.user, cid)
    session = get_object_or_404(CourseSession, pk=sid, course=course, is_template=False)
    labels = course.queue_labels()
    member = CourseBase.get_course_member(request.user, course.pk)

    roll_calls = []
    for check in session.attendance_checks.order_by("started_at", "pk"):
        records = list(
            check.records.select_related("member__user", "member__user__profile", "reviewed_by")
        )
        for record in records:
            record.name = _name(record.member)
            record.list_labels = [labels.get(p, str(p)) for p in record.lists_raised]
        records.sort(key=lambda r: r.name.lower())
        by = lambda *statuses: [r for r in records if r.status in statuses]
        absent = sorted(attendance.absent_students(check), key=lambda m: _name(m).lower())
        for m in absent:
            m.name = _name(m)
        roll_calls.append({
            "check": check,
            "announced_label": labels[check.announced_list],
            "summary": attendance.summary(check),
            "follow_up": by(Status.NOT_IN_CLASS),
            "unreviewed": by(Status.UNREVIEWED),
            "confirmed": by(Status.CONFIRMED),
            "added": by(Status.ADDED),
            "present": by(Status.PRESENT),
            "absent": absent,
        })

    return render(request, "course-lecture.html", {
        "course": course,
        "session": session,
        "numbers": _lecture_numbers(course, session),
        "roll_calls": roll_calls,
        "is_instructor": bool(request.user.is_superuser or (member and member.is_instructor())),
    })


@login_required
def attendance_csv(request, cid, sid):
    course = get_object_or_404(Course, pk=cid)
    CoursePermissions.require_course_staff(request.user, cid)
    session = get_object_or_404(CourseSession, pk=sid, course=course, is_template=False)
    labels = course.queue_labels()

    response = HttpResponse(content_type="text/csv")
    stamp = session.session_date.isoformat() if session.session_date else str(session.pk)
    response["Content-Disposition"] = f'attachment; filename="attendance-{course.pk}-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Roll call", "Announced list", "Student name", "Student ID", "Email",
        "Attended", "Outcome", "Lists raised",
    ])
    for number, check in enumerate(session.attendance_checks.order_by("started_at", "pk"), 1):
        rows = []
        for record in check.records.select_related("member__user", "member__user__profile"):
            rows.append((
                _name(record.member), record.member.get_user_id(), record.member.user.email,
                "yes" if record.attended else "no", record.get_status_display(),
                " / ".join(labels.get(p, str(p)) for p in record.lists_raised),
            ))
        for member in attendance.absent_students(check):
            rows.append((
                _name(member), member.get_user_id(), member.user.email,
                "no", "No hand raised", "",
            ))
        for row in sorted(rows, key=lambda r: r[0].lower()):
            writer.writerow(
                [_safe_csv_cell(v) for v in (number, labels[check.announced_list], *row)]
            )
    return response
