"""The one place anything asks what a contribution is worth.

Agora used to answer that question twice. The tally students watched during a
lecture came from four columns on the course; the grade came from four fields
on the participation scheme, computed from the hand raises themselves. Nothing
kept the two in step, so an instructor could raise what a hand was worth, see
the lecture screen agree, and never learn the grade had not moved. Worse for
the student, who watched a number climb that was not their grade.

There is one system now. The scheme decides, the lecture screen shows a running
total of the grade, and the course columns survive only for a course that has
no scheme yet -- which, after the migration that came with this module, means
a course created by something that does not know about schemes.
"""

from dataclasses import dataclass

from .models import AttendanceRecord


@dataclass(frozen=True)
class Rules:
    """What one contribution is worth, and the most a lecture can hold."""

    raise_value: float = 1.0
    speak_bonus: float = 0.0
    lecture_cap: int | None = None
    bonus_after_speaking: bool = False

    @property
    def speaking_total(self):
        """A raise that ends in speaking, all in."""
        return self.raise_value + self.speak_bonus


def for_course(course):
    """The rules in force, from the scheme where there is one."""
    scheme = getattr(course, "participation_scheme", None)
    if scheme is not None:
        return Rules(
            raise_value=scheme.raise_value,
            speak_bonus=scheme.speak_bonus,
            lecture_cap=scheme.lecture_cap,
            bonus_after_speaking=scheme.bonus_after_speaking,
        )
    # No scheme: the columns that drove Agora before schemes existed. Reached
    # only by a course created outside the settings page, which seeds one.
    return Rules(
        raise_value=course.hand_raise_point,
        speak_bonus=course.speaking_point,
        lecture_cap=course.max_hand_raise_points,
        bonus_after_speaking=course.allow_bonus,
    )


def graded_measures(course):
    """Which measures this course's grade actually rests on."""
    scheme = getattr(course, "participation_scheme", None)
    if scheme is None:
        return set()
    return {
        rubric.measure
        for rubric in scheme.rubrics.all()
        if (rubric.weight or 0) > 0
    }


def lecture_status(
    course, member, session, points_this_lecture=0, bonus=0, answered_roll_call=None
):
    """What to tell a student about the lecture they are sitting in.

    Deliberately about this lecture and nothing else. The term grade is a
    different question, answered on their participation page once the term has
    something to say -- it depends on the whole class and on lectures that have
    not happened, so a number climbing on the screen in front of them cannot
    honestly be it.

    What is worth showing depends on what the course grades. Where attendance
    is the grade, the only fact that matters in the room is whether this
    lecture counts yet, and that is a yes or a no. Where contributions are
    graded too, how many so far. Where every lecture is graded on its own, the
    lecture's own score is the thing.

    ``points_this_lecture`` and ``bonus`` are what the recorder has written
    for this student in this lecture -- the same numbers the grade is computed
    from (see :mod:`participation_schemes`). This used to count ``HandRaise``
    rows instead, so the screen told a student who had toggled their hand
    twenty times that they had twenty contributions while the grade, and the
    published rule, said otherwise. One number, from one place.
    """
    # A lecture the course does not grade -- a first meeting, a guest lecture,
    # a day nobody is teaching. The screen used to say "this lecture counts for
    # you" in one, because nothing here asked. Telling a student their hand
    # counted when it did not is worse than telling them nothing.
    graded_lecture = session is not None and (
        session.counts_for_participation and not session.cancelled
    )

    measures = graded_measures(course)
    credit = (points_this_lecture or 0) + (bonus or 0)
    scheme = getattr(course, "participation_scheme", None)
    # Under AI 100's rubric the first counted raise in a lecture is the
    # attendance; every raise after it is participation. Said that way in the
    # room, so a student is not left subtracting one in their head.
    excludes_covered = bool(scheme is not None and scheme.volume_excludes_covered)
    contributions = points_this_lecture or 0
    # A roll call answered also counts as attending (participation_schemes),
    # and then the first raise did not have to pay for it.
    # The status poll passes this in, having asked in the same query as
    # whether a roll call is open: it is fetched by every phone in the room.
    if answered_roll_call is None:
        answered_roll_call = bool(
            graded_lecture
            and getattr(member, "pk", None)
            and AttendanceRecord.objects.filter(
                roll_call__session=session,
                roll_call__closed_at__isnull=False,
                member=member,
                status__in=AttendanceRecord.ATTENDED,
            ).exists()
        )
    answered_roll_call = bool(graded_lecture and answered_roll_call)
    additional = (
        max(0, contributions - 1)
        if excludes_covered and not answered_roll_call
        else contributions
    )

    # Explicit flags rather than None-for-absent: a Django template cannot ask
    # whether a value is None, and comparing against an unresolvable `None`
    # silently makes every branch false.
    return {
        # False for a lecture that is not graded, so the screen can say so
        # rather than reporting on a grade this lecture does not feed.
        "graded_lecture": graded_lecture,
        "shows_attendance": graded_lecture and "coverage" in measures,
        "counted": credit > 0 or answered_roll_call,
        "shows_contributions": graded_lecture and "volume" in measures,
        "contributions": contributions,
        "excludes_covered": excludes_covered,
        "additional": additional,
        "shows_points": graded_lecture and "per_lecture" in measures,
        "points": points_this_lecture or 0,
        "bonus": bonus or 0,
        "bonus_after_speaking": for_course(course).bonus_after_speaking,
        "any": bool(measures),
    }
