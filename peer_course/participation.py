"""Per-session participation grades.

Agora grades participation independently within each session: the median and
maximum come from students' nonzero real participation in that session, and an
individual grade adds their phantom/bonus points on top. Course-wide totals are
deliberately not part of this rubric, so a quiet week cannot drag down a
student who participated fully in it.

Lives in peer_course because the legacy essay gradebook app (peer_grade) was
removed from Agora; participation grading is Agora's own.
"""

import statistics

from django.db.models import Sum

from .models import CourseMember, CourseParticipation, CourseSession


def calculate_participation_grade(participation_total, median_total, maximum_total):
    """Return the participation grade out of 10 for one session total."""
    total = max(0, participation_total or 0)
    if total == 0:
        return 0.0

    # The maximum threshold is the largest applicable criterion in the rubric.
    if maximum_total and total >= maximum_total:
        return 10.0

    if median_total and total >= median_total:
        if maximum_total > median_total:
            grade = 8 + (2 * (total - median_total) / (maximum_total - median_total))
            return round(min(10.0, grade), 2)
        return 8.0

    if total <= 4:
        return {1: 5.0, 2: 6.0, 3: 6.5, 4: 7.0}.get(total, 0.0)

    if median_total and median_total > 4:
        grade = 7 + ((total - 4) / (median_total - 4))
        return round(min(8.0, grade), 2)

    # This is only reachable for unusual fractional totals below a median <= 4.
    return 7.0


def _session_label(session):
    date = session.session_date.strftime("%Y-%m-%d") if session.session_date else "Date not set"
    start = session.start_time_pst.strftime("%H:%M") if session.start_time_pst else "?"
    end = session.end_time_pst.strftime("%H:%M") if session.end_time_pst else "?"
    return f"{date} ({start} – {end})"


def course_session_participation_grades(course):
    """Return independently calculated participation grades for each session.

    Within each session, median and maximum use students' nonzero main (real)
    participation. Individual grades use main plus phantom/bonus participation.
    Course-wide totals are deliberately not used by this rubric.
    """
    students = list(CourseMember.objects.filter(
        course=course,
        role="student",
        active=True,
    ).order_by("user__last_name", "user__first_name", "id"))
    student_ids = [student.id for student in students]

    rows = CourseParticipation.objects.filter(
        participant_id__in=student_ids,
        count_in_calculations=True,
        session__isnull=False,
    ).values("session_id", "participant_id").annotate(
        main=Sum("participation_points_gained"),
        bonus=Sum("bonus_points"),
    )
    aggregates = {
        (row["session_id"], row["participant_id"]): {
            "main": row["main"] or 0,
            "bonus": row["bonus"] or 0,
        }
        for row in rows
    }
    session_ids = {session_id for session_id, _student_id in aggregates}
    sessions = CourseSession.objects.filter(
        course=course,
        is_template=False,
        id__in=session_ids,
    ).order_by("session_date", "start_time_pst", "id")

    results = []
    for session in sessions:
        main_totals = {
            student.id: aggregates.get((session.id, student.id), {}).get("main", 0)
            for student in students
        }
        nonzero_real_totals = [total for total in main_totals.values() if total > 0]
        median_total = statistics.median(nonzero_real_totals) if nonzero_real_totals else 0
        maximum_total = max(nonzero_real_totals, default=0)

        student_results = {}
        for student in students:
            aggregate = aggregates.get((session.id, student.id), {})
            main = aggregate.get("main", 0)
            bonus = aggregate.get("bonus", 0)
            total = main + bonus
            grade = calculate_participation_grade(total, median_total, maximum_total)
            student_results[student.id] = {
                "main": main,
                "bonus": bonus,
                "total": total,
                "grade": grade,
                "percentage": round(grade * 10, 1),
            }

        results.append({
            "session": session,
            "label": _session_label(session),
            "median": median_total,
            "maximum": maximum_total,
            "students": student_results,
        })

    return results
