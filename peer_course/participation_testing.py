"""Fixtures for tests that need a student to have taken part in a lecture.

Grades are computed from ``CourseParticipation`` -- the record of hands that
were up when the instructor called on someone -- and not from ``HandRaise``,
the audit trail of every hand going up and down. A test that wants a student
to have "contributed three times" therefore writes what the recorder would
have written, priced the way the recorder prices it, and this is the one
place that knows how.
"""

from django.utils import timezone

from . import participation_rules
from .models import CourseParticipation


def recorded_credit(member, session):
    """``(points, bonus)`` recorded for ``member`` in ``session``, as the
    lecture page computes them before asking :func:`lecture_status`."""
    points = bonus = 0
    for row in CourseParticipation.objects.filter(
        participant=member, session=session, count_in_calculations=True
    ):
        points += row.participation_points_gained or 0
        bonus += row.bonus_points or 0
    return points, bonus


def record_contributions(
    member, session, times=1, *, spoke=0, bonus=0, participation_list=1, at=None
):
    """Record ``times`` contributions in ``session``, ``spoke`` of which spoke.

    Written as the recorder writes them: one accumulating row for the calls
    made while the hand was up, one row per time the student was chosen (which
    counts as a contribution and carries the speaking bonus), and ``bonus`` as
    credit paid after speaking. The scheme's ``lecture_cap`` bounds the credit
    a lecture pays, as it does in a real lecture.
    """
    course = session.course
    rules = participation_rules.for_course(course)
    # A queue the course does not grade is recorded but does not count,
    # exactly as the recorder writes it.
    counts = course.list_counts_for_points(participation_list)
    when = at or timezone.now()
    spoke = min(spoke, times)
    waiting = times - spoke

    credit = 0.0
    rows = []

    def room_for(amount):
        nonlocal credit
        if rules.lecture_cap is not None:
            amount = max(0, min(amount, rules.lecture_cap - credit))
        credit += amount
        return amount

    for _ in range(spoke):
        rows.append(
            CourseParticipation(
                participant=member,
                session=session,
                time_participated=when,
                participation_list=participation_list,
                participation_points_gained=round(room_for(rules.speaking_total)),
                spoke_upon_participation=True,
                real_participation=True,
                count_in_calculations=counts,
            )
        )
    if waiting or not rows:
        rows.append(
            CourseParticipation(
                participant=member,
                session=session,
                time_participated=when,
                participation_list=participation_list,
                participation_points_gained=round(
                    room_for(waiting * rules.raise_value)
                ),
                spoke_upon_participation=False,
                real_participation=False,
                count_in_calculations=counts,
            )
        )
    if bonus:
        rows[0].bonus_points = round(room_for(bonus))
    return CourseParticipation.objects.bulk_create(rows)
