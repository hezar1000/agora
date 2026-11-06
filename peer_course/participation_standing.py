"""What one student sees about their own participation.

Three pictures, each answering a question a percentage cannot:

``curve``     what the grade does as contributions rise, with this student and
              the class median marked. Under a class-relative scheme this is
              the whole story -- the grade depends on where the median is, and
              a number alone hides that.
``lectures``  one cell per lecture, so "I have missed two and have one drop
              left" is visible at a glance.
``histogram`` the shape of the class with the student's own bar highlighted.
``lectures``  under a per-lecture scheme, one bar per lecture against the
              median, because the term total is not what that grade depends on.

Only the pictures a course's own scheme depends on are built. Showing a
term-total curve to a class graded lecture by lecture is worse than showing
nothing: it implies a relationship that does not exist, and a student would
reasonably act on it.

The histogram deliberately shows no names, no ranks and no ordering of
individuals. A student can see whether they sit in a crowded middle or a thin
tail, which is what makes their own grade legible, and nothing about anybody
else. Participation is not a leaderboard and turning it into one would change
how people behave in a room.
"""

from collections import Counter
from datetime import date, time

from . import participation_curves as curves
from . import participation_schemes as schemes
from .models import CourseSession, ParticipationScheme


def _rubric_for(scheme, measure):
    return scheme.rubrics.filter(measure=measure).first()


def curve_points(scheme, rubric, population, upper=None, samples=40):
    """``[(contributions, fraction)]`` tracing the curve a student is graded on."""
    if rubric is None:
        return []
    ceiling = upper or max([*population, 1])
    ceiling = max(ceiling, 1)
    step = max(ceiling / samples, 1e-9)
    points, value = [], 0.0
    while value <= ceiling + step / 2:
        points.append((value, schemes.apply_rubric(rubric, value, population)))
        value += step
    return points


def histogram(population, buckets=10):
    """Counts per bucket, as ``[(low, high, count)]``.

    Whole numbers get one bucket each when the range is small, because a
    histogram of counts should not smear three contributions and four together
    when the difference is a grade.
    """
    if not population:
        return []
    low, high = min(population), max(population)
    if high == low:
        return [(low, low, len(population))]
    if high - low + 1 <= buckets:
        tally = Counter(population)
        return [
            (value, value, tally.get(value, 0))
            for value in range(int(low), int(high) + 1)
        ]
    if all(float(value).is_integer() for value in population):
        # Counts get whole-number buckets -- "12–14", never "11.7–13".
        import math

        low, high = int(low), int(high)
        width = math.ceil((high - low + 1) / buckets)
        result = []
        start = low
        while start <= high:
            end = min(start + width - 1, high)
            count = sum(1 for value in population if start <= value <= end)
            result.append((start, end, count))
            start = end + 1
        return result
    width = (high - low) / buckets
    edges = [low + width * index for index in range(buckets + 1)]
    result = []
    for index in range(buckets):
        start, end = edges[index], edges[index + 1]
        if index == buckets - 1:
            count = sum(1 for value in population if start <= value <= end)
        else:
            count = sum(1 for value in population if start <= value < end)
        result.append((start, end, count))
    return result


def lecture_grades(course, scheme, rubric, member, scores):
    """One entry per lecture: what the student earned, and what the median did.

    Sixteen lectures would be sixteen curves, which nobody can read. Each
    lecture reduces to a single number instead -- the grade it awarded -- and
    the median beside it says what taking part normally was worth. Under
    CPSC 430's rubric the median is 80% in almost every lecture, so the line is
    stable enough to read a bar against.
    """
    if rubric is None:
        return []

    curves_by_lecture = schemes.lecture_curves(rubric, scores)
    entries = []
    for session_id in sorted(scores):
        session_scores = scores[session_id]
        population = [value for value in session_scores.values() if value > 0]
        if not population:
            continue
        mine = session_scores.get(member.id, 0)
        curve = curves_by_lecture[session_id]
        entries.append(
            {
                "session_id": session_id,
                "contributions": mine,
                "grade": schemes.apply_rubric(curve, mine, population),
                "median_grade": schemes.apply_rubric(
                    curve, curves.value_at_quantile(population, 0.5), population
                ),
                "took_part": mine > 0,
                "counted": True,
            }
        )

    # The lowest grades are the ones dropped, so say which they were rather
    # than leaving the student to work out why the average is higher than the
    # bars suggest.
    drops = min(scheme.dropped_lectures, max(len(entries) - 1, 0))
    if drops:
        weakest = sorted(
            range(len(entries)), key=lambda index: entries[index]["grade"]
        )[:drops]
        for index in weakest:
            entries[index]["counted"] = False
    return entries


def lecture_rows(
    course, scheme, member, scores, tallies, per_lecture=(), attendance=None, roll_calls=None
):
    """One row per lecture that ran, oldest first, for the student's table.

    Each row carries the lecture itself, the contributions the student was
    credited with in it (hands up when someone was called, and separately any
    credit paid after they spoke), the class median of contributions among
    those who took part, and -- under a per-lecture scheme -- that lecture's
    grade and whether it is one of the dropped ones.
    """
    roll_calls = roll_calls or {}
    if attendance is None:
        attendance = schemes.attendance_by_lecture(scores, roll_calls)
    sessions = {
        session.id: session
        for session in CourseSession.objects.filter(id__in=list(attendance))
    }
    graded_by_session = {entry["session_id"]: entry for entry in per_lecture}
    ordered = sorted(
        attendance,
        key=lambda session_id: (
            sessions[session_id].session_date or date.min,
            sessions[session_id].start_time_pst or time.min,
            session_id,
        )
        if session_id in sessions
        else (date.min, time.min, session_id),
    )
    rows = []
    for index, session_id in enumerate(ordered, start=1):
        session_scores = scores.get(session_id, {})
        population = [value for value in session_scores.values() if value > 0]
        tally = tallies.get(session_id, {}).get(member.id, {})
        graded = graded_by_session.get(session_id)
        rows.append(
            {
                "number": index,
                "session": sessions.get(session_id),
                "session_id": session_id,
                "raised": tally.get("raised", 0),
                "bonus": tally.get("bonus", 0),
                "spoke": tally.get("spoke", 0),
                "contributions": session_scores.get(member.id, 0),
                "took_part": member.id in attendance[session_id],
                "roll_call": member.id in roll_calls.get(session_id, ()),
                "median": (
                    curves.value_at_quantile(population, 0.5) if population else 0
                ),
                "maximum": max(population, default=0),
                "grade": graded["grade"] if graded else None,
                "percent": round(graded["grade"] * 100) if graded else None,
                "counted": graded["counted"] if graded else True,
            }
        )
    return rows


def grade_breakdown(scheme, graded):
    """Each part of the grade as a percentage and marks earned.

    The configured rubric supplies the available marks.  Showing ``4 / 5``
    and ``7 / 10`` lets a student compare the result directly with the course
    schema instead of translating two percentages themselves.
    """
    rubrics = [
        rubric for rubric in scheme.rubrics.all() if (rubric.weight or 0) > 0
    ]
    if not rubrics:
        return []
    lines = []
    for rubric in sorted(rubrics, key=lambda r: r.measure):
        fraction = graded.get(rubric.measure, 0.0)
        lines.append(
            {
                "measure": rubric.measure,
                "label": rubric.get_measure_display(),
                "fraction": fraction,
                "percent": round(fraction * 100),
                "earned": round(fraction * rubric.weight, 2),
                "available": round(rubric.weight, 2),
            }
        )
    return lines


def standing_for(course, member):
    """Everything the student's own page needs, or ``None`` without a scheme."""
    scheme = getattr(course, "participation_scheme", None)
    if scheme is None:
        return None

    tallies = schemes.lecture_tallies(course)
    scores = schemes.lecture_scores(course, scheme, tallies)
    roll_calls = schemes.roll_call_attendance(course)
    attendance = schemes.attendance_by_lecture(scores, roll_calls)
    values = schemes.measure_values(course, scheme, scores, roll_calls)
    mine = values.get(member.id)
    if mine is None:
        return None

    graded = schemes.grade_course(course) or {}
    my_grade = graded.get(member.id, {})

    volume_rubric = _rubric_for(scheme, ParticipationScheme.VOLUME)
    per_lecture_rubric = _rubric_for(scheme, ParticipationScheme.PER_LECTURE)
    # Only measures that carry weight shape the grade, so only those are worth
    # drawing. A measure with no weight is configuration left lying around.
    weighted = {
        rubric.measure
        for rubric in scheme.rubrics.all()
        if (rubric.weight or 0) > 0
    }
    volume_population = [
        other[ParticipationScheme.VOLUME] for other in values.values()
    ]
    my_volume = mine[ParticipationScheme.VOLUME]

    # Lectures in order, each either taken part in or not. Only lectures that
    # ran appear; a session nobody joined was not a lecture.
    lecture_cells = []
    for session_id in sorted(attendance):
        lecture_cells.append(
            {
                "session_id": session_id,
                "took_part": member.id in attendance[session_id],
                "contributions": scores.get(session_id, {}).get(member.id, 0),
            }
        )

    median = (
        curves.value_at_quantile(volume_population, 0.5)
        if volume_population
        else 0
    )
    volume_target = None
    if volume_rubric is not None:
        fixed_positions = [
            anchor.get("at")
            for anchor in volume_rubric.absolute_anchors or []
            if isinstance(anchor.get("at"), (int, float))
        ]
        if fixed_positions:
            volume_target = max(fixed_positions)
    # The running total of the grade itself. A student watching a number climb
    # during a lecture should be watching this one; before, they watched a
    # separate points currency that no grade was computed from.
    weights = [rubric.weight or 0 for rubric in scheme.rubrics.all()]
    marks_available = sum(weights)
    marks_so_far = (my_grade.get("total", 0.0) or 0.0) * marks_available

    per_lecture = (
        lecture_grades(course, scheme, per_lecture_rubric, member, scores)
        if ParticipationScheme.PER_LECTURE in weighted
        else []
    )
    rows = lecture_rows(
        course, scheme, member, scores, tallies, per_lecture, attendance, roll_calls
    )

    # Class context is shown only where the rubric compares the student to
    # the class on a term-wide quantity. Under a per-lecture rubric the class
    # is already inside each lecture's median and maximum ticks.
    shows_class = bool(
        ParticipationScheme.VOLUME in weighted
        and volume_rubric
        and volume_rubric.relative_anchors
    )
    distribution = None
    if shows_class:
        distribution = {
            "buckets": histogram(volume_population),
            "mine": my_volume,
            "median": median,
            "maximum": max(volume_population, default=0),
            "students": len(volume_population),
        }

    # The quantities the marks are computed from, in the rubric's own units.
    # Marks appear only where the rubric itself settles them: a lecture's
    # grade under a per-lecture rubric is final when the lecture ends.
    headline = []
    if ParticipationScheme.PER_LECTURE in weighted:
        graded_lectures = [entry["grade"] for entry in per_lecture]
        headline.append({
            "kind": "per_lecture",
            "value": (
                round(sum(graded_lectures) / len(graded_lectures) * 100)
                if graded_lectures else None
            ),
            "lectures": len(graded_lectures),
            "dropped": scheme.dropped_lectures,
        })
    attendance_target = max(
        (scheme.lectures_expected or len(attendance)) - scheme.dropped_lectures, 1
    )
    if ParticipationScheme.COVERAGE in weighted:
        headline.append({
            "kind": "coverage",
            "value": sum(1 for cell in lecture_cells if cell["took_part"]),
            "target": attendance_target,
            "held": len(attendance),
        })
    if ParticipationScheme.VOLUME in weighted:
        headline.append({
            "kind": "volume",
            "value": my_volume,
            "target": volume_target,
            "median": median if shows_class else None,
            "maximum": max(volume_population, default=0) if shows_class else None,
        })

    # Drops are marked on the chart only once the term is long enough for
    # them to mean something: with four drops and two lectures held, fading
    # one of the two would say more about the calendar than the student.
    drops_visible = len(attendance) > 2 * scheme.dropped_lectures
    for row in rows:
        row["faded"] = drops_visible and not row["counted"]
        # Under AI 100's rubric the first counted raise in a lecture is the
        # attendance, drawn as its own segment.
        row["attendance_part"] = (
            1 if (scheme.volume_excludes_covered and row["took_part"]) else 0
        )
        # Unless the roll call paid for the attendance instead.
        paid_by_raise = 0 if row["roll_call"] else row["attendance_part"]
        row["additional"] = max(0, row["raised"] + row["bonus"] - paid_by_raise)

    return {
        "scheme": scheme,
        "grade": my_grade,
        # One line per lecture that ran: what this student earned in it and
        # what it did to the grade. The complaint that led to this was a
        # student who could not find out how many hand raises they had been
        # credited with in each class -- the charts said percentages and the
        # audit table said "raised" and "lowered".
        "lecture_rows": rows,
        "latest_lecture": rows[-1] if rows else None,
        "distribution": distribution,
        "headline": headline,
        "drops_visible": drops_visible,
        "splits_attendance": bool(scheme.volume_excludes_covered),
        "totals": {
            "raised": sum(row["raised"] for row in rows),
            "bonus": sum(row["bonus"] for row in rows),
            "spoke": sum(row["spoke"] for row in rows),
        },
        # Whether the course pays credit after a student has spoken, so the
        # table knows to show that column.
        "shows_bonus": bool(scheme.bonus_after_speaking),
        "marks_so_far": round(marks_so_far, 2),
        "marks_available": round(marks_available, 2),
        # A class-relative curve is settled on the term's final numbers, so
        # today's figure can still move even for work already done. Saying so
        # is the difference between a running total and a promise.
        "marks_can_still_move": any(
            rubric.relative_anchors and rubric.combination != "absolute"
            for rubric in scheme.rubrics.all()
        ),
        # Each part against the marks assigned to it in the rubric.
        "breakdown": grade_breakdown(scheme, my_grade),
        "volume": my_volume,
        "volume_target": volume_target,
        "volume_median": median,
        "volume_population": volume_population,
        "percentile": round(
            curves.rank_fraction(volume_population, my_volume) * 100
        ),
        "coverage": mine[ParticipationScheme.COVERAGE],
        "lectures": lecture_cells,
        # The strip already said this in its <desc> for screen readers; the
        # page needs it too, for the people looking at the squares.
        "coverage_count": sum(1 for cell in lecture_cells if cell["took_part"]),
        "lectures_counted": len(attendance),
        "attendance_target": max(
            (scheme.lectures_expected or len(attendance)) - scheme.dropped_lectures,
            1,
        ),
        "dropped": scheme.dropped_lectures,
        "curve": (
            curve_points(scheme, volume_rubric, volume_population)
            if ParticipationScheme.VOLUME in weighted
            else []
        ),
        "histogram": (
            histogram(volume_population)
            if ParticipationScheme.VOLUME in weighted
            else []
        ),
        "shows_volume": ParticipationScheme.VOLUME in weighted,
        "shows_coverage": ParticipationScheme.COVERAGE in weighted,
        "lecture_grades": per_lecture,
        "shows_class": shows_class,
    }
