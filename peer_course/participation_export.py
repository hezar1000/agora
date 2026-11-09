"""What leaves Agora as a spreadsheet.

The export used to be the database in CSV form: four columns per lecture named
after internal quantities, every session row including the weekly patterns, and
-- for a feature whose entire purpose is a participation grade -- no grade
anywhere in the file. An instructor wanting to put marks in Canvas had to
reimplement the rubric in a spreadsheet, which is exactly the arithmetic Agora
exists to do.

Three reports, because there are three real questions:

``grades``    what goes in the gradebook: one row per student, the marks.
``lectures``  how each student got there: one column per lecture, hand raises
              counted (up when the instructor called on someone).
``full``      everything recorded, for settling a dispute.

Every file opens with a few comment lines naming the course, the moment of
export and the rubric in force, so a spreadsheet found six months later can
still say what it is.
"""

from collections import defaultdict

from django.utils.timezone import now

import pytz

from . import participation_schemes as schemes
from . import participation_summary
from .sessions import held_lectures
from .models import (
    CourseMember,
    CourseParticipation,
    CourseSession,
    HandRaise,
    ParticipationScheme,
)

GRADES = "grades"
LECTURES = "lectures"
FULL = "full"
KINDS = (GRADES, LECTURES, FULL)

#: Legacy query values, kept so old links and bookmarks still work.
ALIASES = {"points_only": GRADES, "all": FULL, "": GRADES}

TITLES = {
    GRADES: "Participation grades",
    LECTURES: "Hand raises by lecture",
    FULL: "Full participation record",
}


def resolve_kind(value):
    value = (value or "").strip().lower()
    return ALIASES.get(value, value if value in KINDS else GRADES)


def lectures_for(course, start_date=None, end_date=None):
    """The course's real lectures, earliest first. Never a weekly pattern.

    Nor a session in which nothing happened; see ``sessions.held_lectures``.
    """
    lectures = held_lectures(
        CourseSession.objects.filter(course=course, is_template=False)
    )
    if start_date and end_date:
        lectures = lectures.filter(
            session_date__gte=start_date, session_date__lte=end_date
        )
    return list(lectures.order_by("session_date", "start_time_pst", "id"))


def students_for(course):
    """Active students with their user rows already loaded.

    Without ``select_related`` the export ran one query per student to print a
    name -- 120 queries for a 120-person course, and the fault grows with the
    class. ``user__profile`` is in there for the same reason: a preferred
    name lives on the profile, so printing a name touches it.
    """
    return list(
        CourseMember.objects.filter(course=course, role="student", active=True)
        .select_related("user", "user__profile")
        .order_by("user__last_name", "user__first_name", "id")
    )


def lecture_label(lecture):
    date = lecture.session_date.strftime("%Y-%m-%d") if lecture.session_date else "no date"
    name = (lecture.name or "").strip()
    label = f"{date} {name}".strip()
    if lecture.cancelled:
        label += " (no lecture)"
    elif not lecture.counts_for_participation:
        label += " (not graded)"
    return label


def raise_counts(course, lectures, students):
    """``{(member_id, session_id): contributions}`` for these lectures.

    The same figure the grade is computed from: hands up when the instructor
    called on someone, plus credit paid after speaking where the course does
    that. Not raw ``HandRaise`` events, which count every toggle of the button.
    """
    tallies = schemes.lecture_tallies(course, sessions=lectures)
    wanted = {student.id for student in students}
    return {
        (member_id, session_id): tally["raised"] + tally["bonus"]
        for session_id, members in tallies.items()
        for member_id, tally in members.items()
        if member_id in wanted and tally["raised"] + tally["bonus"] > 0
    }


def _attended(counts, roll_calls, student, lecture):
    """The grade's own rule: a contribution, or the roll call counted them."""
    return (
        counts.get((student.id, lecture.id), 0) > 0
        or student.id in roll_calls.get(lecture.id, ())
    )


def _preamble(course, kind, tz):
    lines = [
        [f"{course.displayname} — {TITLES[kind]}"],
        [f"Exported {now().astimezone(tz):%Y-%m-%d %H:%M %Z} from Agora"],
    ]
    summary = participation_summary.for_course(course)
    if summary:
        lines.append([f"Participation is graded out of {summary['total']} marks."])
        for measure in summary["measures"]:
            lines.append([f"{measure['heading']}: {measure['share']['phrase']}."])
            for sentence in measure["sentences"]:
                lines.append([f"    {sentence}"])
        for note in summary["lines"]:
            lines.append([note])
    else:
        lines.append(["This course has no participation scheme, so it has no marks."])
    lines.append([])
    return lines


def _identity_columns():
    return ["Student name", "Student ID", "Email"]


def _identity(student):
    return [
        student.get_user_fullname().strip() or student.user.username,
        student.get_user_id(),
        student.user.email,
    ]


def build(course, kind=GRADES, start_date=None, end_date=None, lists=None):
    """Rows for one report, preamble included."""
    kind = resolve_kind(kind)
    tz = pytz.timezone(course.course_timezone)
    lectures = lectures_for(course, start_date, end_date)
    students = students_for(course)
    rows = _preamble(course, kind, tz)

    scheme = getattr(course, "participation_scheme", None)
    graded = schemes.grade_course(course) if scheme is not None else None
    weights = {}
    if scheme is not None:
        weights = {r.measure: r.weight for r in scheme.rubrics.all()}
    total_weight = sum(weights.values())

    if kind == GRADES:
        header = _identity_columns()
        if ParticipationScheme.COVERAGE in weights:
            header += ["Lectures attended", f"Attendance (of {weights[ParticipationScheme.COVERAGE]:g})"]
        if ParticipationScheme.VOLUME in weights:
            header += ["Bonus outside lectures", "Contributions counted", f"Contributions (of {weights[ParticipationScheme.VOLUME]:g})"]
        if ParticipationScheme.PER_LECTURE in weights:
            header += [f"Per-lecture (of {weights[ParticipationScheme.PER_LECTURE]:g})"]
        header += [f"Participation (of {total_weight:g})", "Participation %"]
        rows.append(header)

        counted = [
            lecture for lecture in lectures
            if lecture.counts_for_participation and not lecture.cancelled
        ]
        counts = raise_counts(course, counted, students)
        roll_calls = schemes.roll_call_attendance(course, sessions=counted)
        extras = schemes.course_bonuses(course)
        for student in students:
            row = _identity(student)
            result = (graded or {}).get(student.id)
            attended = sum(
                1 for lecture in counted
                if _attended(counts, roll_calls, student, lecture)
            )
            if ParticipationScheme.COVERAGE in weights:
                marks = (result or {}).get(ParticipationScheme.COVERAGE, 0.0)
                row += [attended, round(marks * weights[ParticipationScheme.COVERAGE], 2)]
            if ParticipationScheme.VOLUME in weights:
                raw = (result or {}).get("raw", {}).get(ParticipationScheme.VOLUME, 0)
                marks = (result or {}).get(ParticipationScheme.VOLUME, 0.0)
                row += [extras.get(student.id, 0), round(raw, 2), round(marks * weights[ParticipationScheme.VOLUME], 2)]
            if ParticipationScheme.PER_LECTURE in weights:
                marks = (result or {}).get(ParticipationScheme.PER_LECTURE, 0.0)
                row += [round(marks * weights[ParticipationScheme.PER_LECTURE], 2)]
            fraction = (result or {}).get("total", 0.0)
            row += [round(fraction * total_weight, 2), round(fraction * 100, 1)]
            rows.append(row)
        return rows

    if kind == LECTURES:
        rows.append(
            _identity_columns()
            + [lecture_label(lecture) for lecture in lectures]
            + ["Lectures attended", "Total hand raises"]
        )
        counts = raise_counts(course, lectures, students)
        roll_calls = schemes.roll_call_attendance(course, sessions=lectures)
        for student in students:
            per_lecture = [
                counts.get((student.id, lecture.id), 0) for lecture in lectures
            ]
            counting = [
                lecture for lecture in lectures
                if lecture.counts_for_participation and not lecture.cancelled
            ]
            rows.append(
                _identity(student)
                + per_lecture
                + [
                    sum(1 for lecture in counting if _attended(counts, roll_calls, student, lecture)),
                    sum(counts.get((student.id, lecture.id), 0) for lecture in counting),
                ]
            )
        return rows

    # FULL: everything recorded, for settling a dispute.
    chosen = [name.strip() for name in (lists or []) if str(name).strip()]
    header = _identity_columns()
    for lecture in lectures:
        label = lecture_label(lecture)
        header += [
            f"{label} — hand raises counted",
            f"{label} — times spoken",
            f"{label} — bonus points",
            f"{label} — points",
        ]
        for name in chosen:
            header += [f"{label} — spoke in {name}"]
    rows.append(header)

    counts = raise_counts(course, lectures, students)
    spoke = defaultdict(int)
    for member_id, session_id in HandRaise.objects.filter(
        member__in=students, session__in=lectures,
        resolution=HandRaise.Resolution.SPOKE,
    ).values_list("member_id", "session_id"):
        spoke[(member_id, session_id)] += 1

    points = defaultdict(lambda: [0, 0])
    by_list = defaultdict(int)
    for row in CourseParticipation.objects.filter(
        participant__in=students, session__in=lectures, count_in_calculations=True
    ).values(
        "participant_id", "session_id", "participation_list",
        "participation_points_gained", "bonus_points", "spoke_upon_participation",
    ):
        key = (row["participant_id"], row["session_id"])
        points[key][0] += row["participation_points_gained"] or 0
        points[key][1] += row["bonus_points"] or 0
        if row["spoke_upon_participation"]:
            by_list[(*key, row["participation_list"])] += 1

    colours = getattr(course, "list_colour_names", None)
    for student in students:
        row = _identity(student)
        for lecture in lectures:
            key = (student.id, lecture.id)
            gained, bonus = points[key]
            row += [
                counts.get(key, 0),
                spoke.get(key, 0),
                bonus,
                gained + bonus,
            ]
            for index, _name in enumerate(chosen, start=1):
                row.append(by_list.get((student.id, lecture.id, index), 0))
        rows.append(row)
    return rows


def filename(course, kind, tz):
    stamp = now().astimezone(tz).strftime("%Y-%m-%d_%H-%M")
    safe = "".join(
        character if character.isalnum() or character in " -_" else "-"
        for character in course.displayname
    ).strip()
    return f"{safe} {TITLES[resolve_kind(kind)].lower()} {stamp}.csv"
