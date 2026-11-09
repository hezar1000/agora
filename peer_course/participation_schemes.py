"""Grade a course's participation from recorded hand raises.

Three measures, each optional and each with its own weight and curve:

``coverage``      how many lectures a student took part in at all
``volume``        contributions added up across the term
``per_lecture``   every lecture graded against that lecture's class, averaged

Attendance is ``coverage`` alone. CPSC 320 is the same thing, reached by capping
each lecture at one contribution. CPSC 430 is ``per_lecture``. The scheme
drafted for AI 100 is ``coverage`` plus ``volume``. They are configuration, not
four implementations.

What a contribution is
-----------------------

A contribution is a hand that was up when the instructor called on someone.
Both syllabi this was built for say so in as many words -- CPSC 430 "records
everyone who raised their hand" each time the instructor presses the button,
and AI 100 "records every student who had their hand raised when called upon"
-- and it is what the tally on the lecture screen has always counted.

Until 2026-09-16 this module counted ``HandRaise`` rows instead: one per time
a hand went up, whether or not anyone was ever called while it was up. That is
not the published rule, and it is trivially gamed. The first week of term
showed students toggling a hand twenty-seven times in eleven seconds and being
graded on twenty-eight contributions where the room had seen them earn four.

The record of contributions is ``CourseParticipation``: one row per student
per lecture per queue accumulating the calls made while their hand was up,
one row per time they were chosen to speak, and ``bonus_points`` for the
calls after they spoke where the course keeps paying (CPSC 430's "as though
you raised your hand throughout the remainder of the class"). The recorder
prices those rows from :mod:`participation_rules`, which reads this same
scheme, so ``raise_value``, ``speak_bonus`` and ``lecture_cap`` apply as a
lecture happens. Everything else about a scheme -- weights, curves, dropped
lectures, the lectures expected, which lectures count -- is applied here and
re-grades work already done when it changes.

``HandRaise`` remains the audit trail of every hand up and down, and is what a
student's "recorded hand raises" table shows.

What attendance is
------------------

A student attended a lecture if they earned a contribution in it, or answered
its roll call (see :mod:`attendance`): a hand in the list the instructor named,
or one settled as "in class" afterwards. A hand in the wrong list that nobody
answered for earns nothing from the roll call.

Under ``volume_excludes_covered`` the first contribution pays for attendance
and is not counted again. Where the roll call already paid for it, it does not
have to, so every contribution in that lecture counts.
"""

from collections import defaultdict

from django.db.models import Count, Q, Sum

from . import participation_curves as curves
from .models import (
    AttendanceCheck,
    AttendanceRecord,
    CourseMember,
    CourseParticipation,
    CourseSession,
    MeasureRubric,
    ParticipationScheme,
)

MEASURES = (
    ParticipationScheme.COVERAGE,
    ParticipationScheme.VOLUME,
    ParticipationScheme.PER_LECTURE,
)


def _anchors(raw):
    return [curves.Anchor(entry["at"], entry["gives"]) for entry in raw or []]


def apply_rubric(rubric, value, population):
    """The fraction of this measure's marks that ``value`` earns."""
    absolute = relative = None
    if rubric.absolute_anchors:
        absolute = curves.evaluate(
            _anchors(rubric.absolute_anchors),
            value,
            population=population,
            interpolation=rubric.absolute_interpolation,
        )
    if rubric.relative_anchors:
        relative = curves.evaluate(
            _anchors(rubric.relative_anchors),
            value,
            population=population,
            interpolation=rubric.relative_interpolation,
            # A class-relative curve describes the top of the distribution, so
            # below its lowest anchor it has no opinion. Clamping instead would
            # award its lowest reward to a student who did nothing.
            applies_below_first=False,
        )
    return curves.combine(absolute, relative, rubric.combination)


def lecture_curves(rubric, session_ids):
    """``{session_id: curves}``: what grades each lecture under ``rubric``.

    The rubric's own curves, except for a lecture dated before one of its
    :class:`~peer_course.models.EarlierCurve` cutoffs, which is graded by the
    earliest such curve -- the table that was in force when it was held.
    """
    earlier = list(rubric.earlier_curves.order_by("until"))
    if not earlier:
        return {session_id: rubric for session_id in session_ids}
    dates = dict(
        CourseSession.objects.filter(id__in=list(session_ids)).values_list(
            "id", "session_date"
        )
    )
    return {
        session_id: next(
            (curve for curve in earlier if dates[session_id] < curve.until),
            rubric,
        )
        for session_id in session_ids
    }


def lecture_tallies(course, sessions=None):
    """``{session_id: {member_id: tally}}`` of what was recorded per lecture.

    Each tally is ``{"raised": points, "bonus": points, "spoke": times}``:
    ``raised`` is the credit earned by having a hand up when someone was
    called (the chosen student's own call included), ``bonus`` the credit
    paid for calls after the student had spoken, ``spoke`` how many times
    they were chosen.

    Only graded lectures appear unless ``sessions`` names the lectures wanted,
    in which case those and only those are tallied, graded or not -- the
    export lists every lecture and says which ones did not count.
    """
    rows = CourseParticipation.objects.filter(
        participant__course=course,
        participant__role="student",
        participant__active=True,
        session__isnull=False,
        # Undo excludes a call without deleting it.
        count_in_calculations=True,
    )
    if sessions is None:
        rows = rows.filter(
            # An excluded lecture keeps its records but counts for nobody.
            # Dropping it here removes it from attendance and absence
            # together, so a first meeting cannot be a lecture anyone missed.
            session__counts_for_participation=True,
            # A day with no lecture is neither attended nor missed.
            session__cancelled=False,
        )
    else:
        rows = rows.filter(session__in=sessions)

    rows = rows.values("session_id", "participant_id").annotate(
        raised=Sum("participation_points_gained"),
        bonus=Sum("bonus_points"),
        spoke=Count("id", filter=Q(spoke_upon_participation=True)),
    )

    tallies = defaultdict(dict)
    for row in rows:
        tallies[row["session_id"]][row["participant_id"]] = {
            "raised": row["raised"] or 0,
            "bonus": row["bonus"] or 0,
            "spoke": row["spoke"] or 0,
        }
    return dict(tallies)


def roll_call_attendance(course, sessions=None):
    """``{session_id: {member_id, ...}}`` for every lecture with a closed roll call.

    Graded lectures only, unless ``sessions`` names the ones wanted, exactly as
    :func:`lecture_tallies` chooses them. A lecture with two roll calls is
    attended by someone who answered either.
    """
    checks = AttendanceCheck.objects.filter(
        course=course, closed_at__isnull=False, session__isnull=False
    )
    if sessions is None:
        checks = checks.filter(
            session__counts_for_participation=True, session__cancelled=False
        )
    else:
        checks = checks.filter(session__in=sessions)
    attended = {session_id: set() for session_id in checks.values_list("session_id", flat=True)}
    records = AttendanceRecord.objects.filter(
        roll_call__in=checks, status__in=AttendanceRecord.ATTENDED
    ).values_list("roll_call__session_id", "member_id")
    for session_id, member_id in records:
        attended[session_id].add(member_id)
    return attended


def attendance_by_lecture(scores, roll_calls):
    """``{session_id: {member_id, ...}}``: who attended each lecture that ran.

    Anyone with a contribution, and anyone the roll call counted. A lecture
    with only a roll call and no contributions still ran.
    """
    attended = {
        session_id: {member for member, score in members.items() if score > 0}
        for session_id, members in scores.items()
    }
    for session_id, members in roll_calls.items():
        attended.setdefault(session_id, set()).update(members)
    return attended


def lecture_scores(course, scheme, tallies=None):
    """``{session_id: {member_id: score}}`` for every student and lecture.

    A student's score in a lecture is what the recorder wrote for it: the
    credit for calls made while their hand was up, plus any credit paid after
    they spoke. ``raise_value``, ``speak_bonus`` and ``lecture_cap`` were
    already applied when each row was written, so a change to them re-grades
    lectures still to come and leaves the record of past ones alone.

    A student with a tally but no credit -- every call they were recorded for
    has since been undone -- did not take part, and is left out, so that the
    lecture's population and their attendance both say so.
    """
    tallies = lecture_tallies(course) if tallies is None else tallies
    scores = {}
    for session_id, members in tallies.items():
        scores[session_id] = {}
        for member_id, tally in members.items():
            score = tally["raised"] + tally["bonus"]
            if score > 0:
                scores[session_id][member_id] = score
        if not scores[session_id]:
            # Every row in this lecture was undone; nothing happened in it.
            del scores[session_id]
    return scores


def _lecture_count(course, scheme, scores, roll_calls=()):
    """How many lectures the term is graded against.

    A session in which nobody raised a hand did not happen as a lecture -- it
    was cancelled, or the queue was never opened. Sessions are scheduled ahead
    and a skipped one leaves its row behind, so the schedule over-counts.

    Counting only sessions with activity also protects students: a lecture that
    offered no chance to participate cannot then be held against them for their
    not having participated in it.

    `lectures_expected` overrides this for a course that knows its own shape --
    sixteen lectures whether or not every one of them ran.
    """
    if scheme.lectures_expected:
        return scheme.lectures_expected
    return len(set(scores) | set(roll_calls)) or 1


def course_bonuses(course):
    """``{member_id: points}`` of credit recorded outside any lecture.

    A ``CourseParticipation`` row with no session is credit the course gave
    for something other than a lecture -- an ORCA familiarization quiz worth
    two hand raises, say. It adds to a student's contributions (``volume``)
    and nothing else: it is not a lecture attended, it has no class to be
    compared against, and it never appears in a lecture's tally. Only
    ``bonus_points`` count on these rows, so that an orphaned call record can
    never turn into credit.
    """
    rows = (
        CourseParticipation.objects.filter(
            participant__course=course,
            participant__role="student",
            participant__active=True,
            session__isnull=True,
            count_in_calculations=True,
        )
        .values("participant_id")
        .annotate(bonus=Sum("bonus_points"))
    )
    return {row["participant_id"]: row["bonus"] for row in rows if (row["bonus"] or 0) > 0}


def measure_values(course, scheme, scores=None, roll_calls=None):
    """``{member_id: {measure: value}}``, before any curve is applied."""
    scores = lecture_scores(course, scheme) if scores is None else scores
    roll_calls = roll_call_attendance(course) if roll_calls is None else roll_calls
    attendance = attendance_by_lecture(scores, roll_calls)
    members = list(
        CourseMember.objects.filter(
            course=course, role="student", active=True
        ).values_list("id", flat=True)
    )
    held = _lecture_count(course, scheme, scores, roll_calls)
    drops = min(scheme.dropped_lectures, max(held - 1, 0))
    graded_lectures = max(held - drops, 1)
    extras = course_bonuses(course)

    values = {}
    for member_id in members:
        attended = sum(1 for members in attendance.values() if member_id in members)
        volume = sum(session.get(member_id, 0) for session in scores.values())
        if scheme.volume_excludes_covered:
            # Only where a raise was what earned the attendance. Where the
            # roll call earned it, every raise in that lecture is a
            # contribution.
            by_raise = sum(
                1 for session_id, session in scores.items()
                if session.get(member_id, 0) > 0
                and member_id not in roll_calls.get(session_id, ())
            )
            # One contribution per lecture attended has already been counted
            # as attendance, and a scheme grading both would otherwise pay
            # twice for the same raise. The raise is deducted rather than the
            # lecture's whole score, so a speaking bonus survives: it was the
            # raise that earned attendance, not being chosen.
            #
            # Deducted for every lecture attended, including any beyond the
            # count attendance is graded out of. That is what AI 100's rubric
            # does in its own worked example -- a student attending fourteen
            # of sixteen lectures has fourteen raises deducted, though
            # attendance is graded out of thirteen.
            volume = max(0.0, volume - by_raise * scheme.raise_value)
        # Credit given outside lectures is a contribution, never attendance.
        volume += extras.get(member_id, 0)
        values[member_id] = {
            # Coverage is capped at 1: the dropped lectures are a grace, not a
            # way to exceed full marks by attending everything.
            ParticipationScheme.COVERAGE: min(attended / graded_lectures, 1.0),
            ParticipationScheme.VOLUME: volume,
        }
    return values


def per_lecture_fractions(course, scheme, rubric, scores=None):
    """Each lecture graded against its own class, then averaged.

    Every lecture is anchored on its own median and maximum, so a quiet week
    cannot depress a student who took part fully in it. Dropped lectures remove
    the lowest grades, which is what makes absence survivable.
    """
    scores = lecture_scores(course, scheme) if scores is None else scores
    members = list(
        CourseMember.objects.filter(
            course=course, role="student", active=True
        ).values_list("id", flat=True)
    )

    curves_by_lecture = lecture_curves(rubric, scores)
    per_member = defaultdict(list)
    for session_id, session_scores in scores.items():
        # The class a lecture is graded against is those who took part in it;
        # including absentees as zeroes would drag every median to nothing.
        population = [value for value in session_scores.values() if value > 0]
        if not population:
            continue
        curve = curves_by_lecture[session_id]
        for member_id in members:
            per_member[member_id].append(
                apply_rubric(curve, session_scores.get(member_id, 0), population)
            )

    held = _lecture_count(course, scheme, scores)
    drops = min(scheme.dropped_lectures, max(held - 1, 0))
    fractions = {}
    for member_id in members:
        graded = sorted(per_member.get(member_id, []), reverse=True)
        graded = graded[: max(held - drops, 1)]
        if not graded:
            fractions[member_id] = 0.0
            continue
        # Averaged over the lectures the term is graded against, so missing one
        # entirely is not the same as attending and staying quiet.
        fractions[member_id] = sum(graded) / max(held - drops, 1)
    return fractions


def grade_course(course):
    """``{member_id: {"total": fraction, measure: fraction}}`` for the course.

    ``total`` is the weighted blend, as a fraction of the participation marks.
    Returns ``None`` when the course has no scheme, which means it keeps the
    behaviour it had before schemes existed.
    """
    scheme = getattr(course, "participation_scheme", None)
    if scheme is None:
        return None

    rubrics = {rubric.measure: rubric for rubric in scheme.rubrics.all()}
    scores = lecture_scores(course, scheme)
    values = measure_values(course, scheme, scores)

    per_lecture = {}
    if ParticipationScheme.PER_LECTURE in rubrics:
        per_lecture = per_lecture_fractions(
            course, scheme, rubrics[ParticipationScheme.PER_LECTURE], scores
        )

    results = {}
    total_weight = sum(rubric.weight for rubric in rubrics.values()) or 1.0
    for member_id, measured in values.items():
        parts, total = {}, 0.0
        for measure, rubric in rubrics.items():
            if measure == ParticipationScheme.PER_LECTURE:
                fraction = per_lecture.get(member_id, 0.0)
            else:
                population = [other[measure] for other in values.values()]
                fraction = apply_rubric(rubric, measured[measure], population)
            parts[measure] = fraction
            total += fraction * rubric.weight
        parts["total"] = total / total_weight
        parts["raw"] = measured
        results[member_id] = parts
    return results
