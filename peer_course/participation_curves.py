"""Turn a participation measure into a fraction of the marks available.

Every scheme Agora has ever run is the same shape: a list of anchor points,
some at fixed counts and some at class statistics, with interpolation between
them, optionally taking the better of an absolute and a class-relative curve.
CPSC 430's per-session rubric and the term-wide scheme drafted for AI 100 are
both expressible here, and ``test_participation_curves`` pins each against the
arithmetic it replaces.

The one choice with real consequences is whether to interpolate by VALUE or by
RANK between two anchors.

  By value, one student who contributes ten times the median stretches the
  scale, and everyone between the median and them is squashed toward the lower
  anchor. By rank, that student is simply top of the class and the people below
  them spread out evenly.

Rank also makes ties come out right by construction: the quantile of a count is
a property of the count, so two students who did identically score identically.
Ranking by position in a sorted list does not have that property, and would
hand two students who each contributed 31 times different grades.
"""

import bisect
import math
from dataclasses import dataclass

#: Anchor positions that resolve against the class rather than a fixed count.
STATISTICS = {
    "min": 0.0,
    "median": 0.5,
    "max": 1.0,
}

BY_VALUE = "value"
BY_RANK = "rank"
STEPPED = "step"
INTERPOLATIONS = (BY_VALUE, BY_RANK, STEPPED)


class CurveError(ValueError):
    """A curve that cannot be evaluated, reported where it can be fixed."""


@dataclass(frozen=True)
class Anchor:
    """``at`` this much, a student earns ``gives`` of the marks available.

    ``at`` is a count, or one of :data:`STATISTICS`, or ``"pNN"`` for the NNth
    percentile. ``gives`` is a fraction in [0, 1], not a point total, so the
    curve is independent of what the measure is worth in the course.
    """

    at: object
    gives: float

    def __post_init__(self):
        if not (0.0 <= self.gives <= 1.0):
            raise CurveError(
                f"anchor gives {self.gives}, which is not a fraction of the "
                "marks available; use 0.8 rather than 8 or 80"
            )
        if isinstance(self.at, str):
            quantile_of(self.at)  # raises if the name is not understood

    @property
    def is_statistic(self):
        return isinstance(self.at, str)


def quantile_of(name):
    """The quantile a statistic name refers to."""
    if name in STATISTICS:
        return STATISTICS[name]
    if name.startswith("p"):
        try:
            percentile = float(name[1:])
        except ValueError:
            pass
        else:
            if 0.0 <= percentile <= 100.0:
                return percentile / 100.0
    raise CurveError(
        f"unknown anchor position {name!r}; expected a number, "
        f"{', '.join(sorted(STATISTICS))}, or a percentile such as 'p90'"
    )


def value_at_quantile(population, quantile):
    """The population value standing at ``quantile``.

    Linear interpolation between order statistics, which is what ``median``
    means for an even-sized class.
    """
    if not population:
        return 0.0
    ordered = sorted(population)
    if len(ordered) == 1:
        return float(ordered[0])
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def rank_fraction(population, value):
    """Where ``value`` stands in ``population``, as a fraction in [0, 1].

    The least-participating student is rank 1 and the most-participating is
    rank n, as in AI 100's published formula.  Ties take the upper rank shared
    by that value, so equal contribution counts still receive equal grades.
    """
    if not population:
        return 0.0
    ordered = sorted(population)
    return bisect.bisect_right(ordered, value) / len(ordered)


def _position(anchor, population, interpolation):
    """Where an anchor sits on the axis this curve interpolates along.

    Under rank interpolation a statistic is its stated quantile.  In
    particular, AI 100's median-to-maximum formula places those anchors at
    ``n/2`` and ``n``.  Subject ranks still resolve from their values so ties
    receive the same result.
    """
    if interpolation == BY_RANK:
        if anchor.is_statistic:
            return quantile_of(anchor.at)
        return rank_fraction(population, anchor.at)
    if anchor.is_statistic:
        return value_at_quantile(population, quantile_of(anchor.at))
    return float(anchor.at)


def _subject_position(value, population, interpolation):
    if interpolation == BY_RANK:
        return rank_fraction(population, value)
    return float(value)


def evaluate(
    anchors, value, *, population=(), interpolation=BY_VALUE,
    applies_below_first=True,
):
    """The fraction of available marks ``value`` earns under this curve.

    ``population`` is every student's measure, used to resolve statistic
    anchors and, under rank interpolation, to place the student.

    ``applies_below_first`` decides what happens beneath the lowest anchor. An
    absolute curve starting at ``0 -> 0%`` wants to clamp there. A curve that
    only describes the top of the class -- ``median -> 80%, max -> 100%`` --
    must instead return ``None``, meaning "no opinion", because clamping would
    award 80% to a student who did nothing.
    """
    if not anchors:
        raise CurveError("a curve needs at least one anchor")
    if interpolation not in INTERPOLATIONS:
        raise CurveError(
            f"unknown interpolation {interpolation!r}; "
            f"expected one of {', '.join(INTERPOLATIONS)}"
        )
    population = list(population)

    # Participation measures are counts. A class-relative top-half curve must
    # not turn a zero into credit merely because the class median and maximum
    # are also zero; the fixed curve is the authority until someone has
    # actually participated.
    if not applies_below_first and float(value) <= 0:
        return None

    # Rank ties can reach the median's numerical rank while their value is
    # still below the median itself (for example fifty 10s followed by fifty
    # 40s have a median of 25).  A top-half curve has no opinion there: the
    # published rule says students *below the median* keep the fixed score.
    if not applies_below_first:
        value_thresholds = [
            value_at_quantile(population, quantile_of(anchor.at))
            if anchor.is_statistic
            else float(anchor.at)
            for anchor in anchors
        ]
        if value_thresholds and float(value) < min(value_thresholds):
            return None

    # A student exactly at a named statistic gets that statistic's stated
    # reward.  This matters in odd-sized classes, where the median student's
    # integer rank lies just above n/2 even though they are the median.
    matching_statistics = [
        anchor.gives
        for anchor in anchors
        if anchor.is_statistic
        and float(value)
        == value_at_quantile(population, quantile_of(anchor.at))
    ]
    if matching_statistics:
        return max(matching_statistics)

    placed = sorted(
        (
            (_position(anchor, population, interpolation), anchor.gives)
            for anchor in anchors
        ),
        # Anchors can collide -- a class where the median equals the maximum is
        # ordinary, not an error. Sorting the reward second means the most
        # generous applicable anchor wins.
        key=lambda item: (item[0], item[1]),
    )
    # Contributing more must never earn less. Statistic anchors move with the
    # class, so a quiet week can drop `max` below a fixed anchor and leave the
    # anchors, in position order, descending -- for a class whose median and
    # maximum are both 1, `max` sits at 1.0 while the fixed anchor at 2 gives
    # 0.60. Carrying the best reward so far forward makes the curve monotone
    # whatever the class does. The rubric this replaces got the same result by
    # testing `total >= maximum` before anything else.
    running, monotone = 0.0, []
    for position, gives in placed:
        running = max(running, gives)
        monotone.append((position, running))
    placed = monotone
    here = _subject_position(value, population, interpolation)

    positions = [position for position, _ in placed]
    index = bisect.bisect_right(positions, here) - 1
    if index < 0:
        if not applies_below_first:
            return None
        # Below every anchor. The lowest anchor is the floor, so a curve that
        # should award nothing below its first threshold says so with a 0 there.
        return placed[0][1]
    if index >= len(placed) - 1:
        return placed[-1][1]

    lower_position, lower_gives = placed[index]
    upper_position, upper_gives = placed[index + 1]
    if interpolation == STEPPED or upper_position <= lower_position:
        return lower_gives
    weight = (here - lower_position) / (upper_position - lower_position)
    return lower_gives + weight * (upper_gives - lower_gives)


BETTER_OF_BOTH = "better"
ABSOLUTE_ONLY = "absolute"
RELATIVE_ONLY = "relative"
COMBINATIONS = (BETTER_OF_BOTH, ABSOLUTE_ONLY, RELATIVE_ONLY)


def combine(absolute=None, relative=None, rule=BETTER_OF_BOTH):
    """Resolve two curves into one fraction."""
    if rule == ABSOLUTE_ONLY:
        return absolute if absolute is not None else 0.0
    if rule == RELATIVE_ONLY:
        return relative if relative is not None else 0.0
    if rule != BETTER_OF_BOTH:
        raise CurveError(
            f"unknown combination {rule!r}; expected one of "
            f"{', '.join(COMBINATIONS)}"
        )
    candidates = [value for value in (absolute, relative) if value is not None]
    return max(candidates) if candidates else 0.0


# --- writing a curve down --------------------------------------------------
#
# Anchors are stored as JSON but nobody should have to type JSON to move a
# threshold. The written form is what an instructor would say out loud:
#
#     0 -> 0%, 1 -> 50%, 4 -> 70%, median -> 80%, max -> 100%
#
# Positions are counts or statistic names; rewards are percentages of the marks
# that measure is worth.

def format_anchors(anchors):
    """Render stored anchors as the written form."""
    parts = []
    for anchor in anchors or []:
        at = anchor["at"] if isinstance(anchor, dict) else anchor.at
        gives = anchor["gives"] if isinstance(anchor, dict) else anchor.gives
        reward = f"{gives * 100:g}%"
        parts.append(f"{at} -> {reward}")
    return ", ".join(parts)


def parse_anchors(text):
    """Read the written form back, reporting what is wrong where.

    Returns a list of ``{"at": ..., "gives": ...}`` ready to store. Raises
    :class:`CurveError` with a message meant for whoever typed it.
    """
    text = (text or "").strip()
    if not text:
        return []

    parsed = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "->" not in piece:
            raise CurveError(
                f"{piece!r} is missing its arrow; write it as '30 -> 60%'"
            )
        left, right = (part.strip() for part in piece.split("->", 1))
        if not left or not right:
            raise CurveError(f"{piece!r} needs a position and a reward")

        try:
            at = float(left)
        except ValueError:
            quantile_of(left)  # raises with a helpful message if unknown
            at = left
        else:
            if at.is_integer():
                at = int(at)
            if at < 0:
                raise CurveError(f"{left!r} is negative; positions are counts")

        reward = right.rstrip("%").strip()
        try:
            gives = float(reward) / 100.0
        except ValueError:
            raise CurveError(
                f"{right!r} is not a percentage; write it as '60%'"
            ) from None
        if not (0.0 <= gives <= 1.0):
            raise CurveError(
                f"{right!r} is outside 0% to 100%"
            )
        parsed.append({"at": at, "gives": gives})

    if not parsed:
        return []

    # Two anchors at one position with different rewards is ambiguous, and the
    # curve would silently take the more generous one.
    seen = {}
    for anchor in parsed:
        position = anchor["at"]
        if position in seen and seen[position] != anchor["gives"]:
            raise CurveError(
                f"{position} is given two different rewards; remove one"
            )
        seen[position] = anchor["gives"]
    return parsed
