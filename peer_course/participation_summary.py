"""What a course's participation scheme does, in sentences, from the settings.

The settings page used to show only the dials: a weight, two curves written as
``0 -> 0%, 28 -> 70%``, and three dropdowns about interpolation. All of it is
true and none of it answers the question an instructor actually has, which is
"so what will a student get?".

These sentences are derived from the same rows the grader reads, so the page
cannot describe a rubric the course does not have. Written for someone who has
never seen Agora before, which is most people opening this page.
"""

from .models import MeasureRubric, ParticipationScheme

#: Small integers read better as words in the middle of a sentence.
_SHARES = {
    (1, 2): "half", (1, 3): "a third", (2, 3): "two thirds",
    (1, 4): "a quarter", (3, 4): "three quarters",
    (1, 5): "a fifth", (2, 5): "two fifths", (3, 5): "three fifths",
    (4, 5): "four fifths",
}


def _fraction_in_words(part, whole):
    """``5`` of ``15`` as "a third", or None when no tidy phrase fits."""
    if not whole or part <= 0 or part >= whole:
        return None
    from math import gcd

    part, whole = int(round(part)), int(round(whole))
    if part <= 0 or whole <= 0:
        return None
    divisor = gcd(part, whole)
    return _SHARES.get((part // divisor, whole // divisor))


def _marks(value):
    """A weight as a number, without a trailing .0 on whole marks."""
    return f"{value:g}"


def share_of(rubric, rubrics):
    """One measure's share of the participation grade, as words and a percent.

    The grader divides each measure's weight by the total, so a weight is a
    share and not a percentage of anything -- which is exactly what reading
    "5" next to "share of the participation grade" fails to convey.
    """
    total = sum(other.weight for other in rubrics) or 1.0
    percent = 100.0 * rubric.weight / total
    words = _fraction_in_words(rubric.weight, total)
    return {
        "weight": _marks(rubric.weight),
        "total": _marks(total),
        "percent": round(percent),
        "words": words,
        "phrase": (
            f"{_marks(rubric.weight)} of {_marks(total)}"
            + (f" — {words} of the participation grade" if words
               else f" — {round(percent)}% of the participation grade")
        ),
    }


def _anchor_value(anchors, target):
    for anchor in anchors or []:
        at = anchor["at"] if isinstance(anchor, dict) else anchor.at
        gives = anchor["gives"] if isinstance(anchor, dict) else anchor.gives
        if at == target:
            return gives
    return None


def _top_anchor(anchors):
    """The highest fixed threshold, as (count, fraction)."""
    numeric = [
        (a["at"] if isinstance(a, dict) else a.at,
         a["gives"] if isinstance(a, dict) else a.gives)
        for a in anchors or []
    ]
    numeric = [(at, gives) for at, gives in numeric if not isinstance(at, str)]
    return max(numeric, key=lambda item: item[0]) if numeric else None


def describe(scheme):
    """The scheme as ``{"total": ..., "lines": [...], "measures": [...]}``.

    ``lines`` are sentences about the scheme as a whole. ``measures`` carry a
    heading, a share and their own sentences, so the page can put each beside
    the fields that produced it.
    """
    if scheme is None:
        return None

    rubrics = list(scheme.rubrics.all())
    if not rubrics:
        return None
    total_weight = sum(rubric.weight for rubric in rubrics) or 1.0

    lines = []
    held = scheme.lectures_expected
    if held:
        graded = max(held - scheme.dropped_lectures, 1)
        if scheme.dropped_lectures:
            lines.append(
                f"The term is graded as {held} lectures, of which the "
                f"{scheme.dropped_lectures} weakest are dropped — so a student "
                f"is marked against {graded} of them."
            )
        else:
            lines.append(f"The term is graded as {held} lectures, none dropped.")
    else:
        lines.append(
            "The term is graded against the lectures actually held, counting "
            "only those in which somebody raised a hand."
            + (f" The {scheme.dropped_lectures} weakest are dropped."
               if scheme.dropped_lectures else "")
        )

    # The rule both syllabi state: a hand counts when it is up as the
    # instructor calls on someone, once per call. Raising and lowering it
    # between calls earns nothing.
    contribution = (
        "A hand raise counts as one contribution each time the instructor "
        "calls on someone while it is up."
    )
    if scheme.raise_value != 1:
        contribution = (
            f"A hand raise is worth {_marks(scheme.raise_value)} contributions "
            "each time the instructor calls on someone while it is up."
        )
    if scheme.speak_bonus:
        contribution += (
            f" Being chosen to speak adds {_marks(scheme.speak_bonus)} more."
        )
    if scheme.lecture_cap == 1:
        contribution += (
            " Only the first counts in any one lecture, so every lecture is "
            "simply attended or not."
        )
    elif scheme.lecture_cap:
        contribution += f" At most {scheme.lecture_cap} count in one lecture."
    lines.append(contribution)

    measures = []
    for rubric in sorted(rubrics, key=lambda r: -r.weight):
        share = share_of(rubric, rubrics)
        sentences = []
        if rubric.measure == ParticipationScheme.COVERAGE:
            graded = max((held or 0) - scheme.dropped_lectures, 1) if held else None
            sentences.append(
                "A lecture counts if the student took part in it at all."
                + (f" Full marks at {graded} lectures; below that, the marks "
                   f"scale with how many." if graded else "")
            )
        elif rubric.measure == ParticipationScheme.VOLUME:
            if scheme.volume_excludes_covered:
                sentences.append(
                    "Every contribution across the term except the one that "
                    "earned each lecture's attendance."
                )
            else:
                sentences.append("Every contribution across the term.")
            top = _top_anchor(rubric.absolute_anchors)
            if top and top[0] > 0:
                count, gives = top
                each = gives * rubric.weight / count
                sentences.append(
                    f"Each one is worth {each:.3g} marks up to {count:g} of "
                    f"them, which reaches {gives * rubric.weight:.3g} of the "
                    f"{_marks(rubric.weight)}. Beyond that the fixed scale "
                    "stops climbing."
                )
        elif rubric.measure == ParticipationScheme.PER_LECTURE:
            if rubric.relative_anchors:
                sentences.append(
                    "Every lecture is graded against that lecture's own class, "
                    "and the results averaged, so a quiet week cannot pull "
                    "down a student who took part fully in it."
                )
            else:
                sentences.append(
                    "Every lecture is graded on the same fixed table, and the "
                    "results averaged."
                )
            for curve in rubric.earlier_curves.order_by("until"):
                sentences.append(
                    f"Lectures before {curve.until:%B} {curve.until.day} keep "
                    "the table that was in force when they were held."
                )

        median = _anchor_value(rubric.relative_anchors, "median")
        top_of_class = _anchor_value(rubric.relative_anchors, "max")
        if median is not None and top_of_class is not None:
            relative = (
                f"Above the class median the marks are curved by rank: the "
                f"median student gets {median * rubric.weight:.3g} of "
                f"{_marks(rubric.weight)}, the most active gets "
                f"{top_of_class * rubric.weight:.3g}, and everyone between is "
                "spread evenly by their place in the class."
            )
            if rubric.combination == "better":
                relative += " Students get whichever of the two pays more."
            sentences.append(relative)
        elif rubric.combination == "absolute":
            sentences.append("Nothing here depends on how the class does.")

        measures.append({
            "measure": rubric.measure,
            "heading": rubric.get_measure_display(),
            "share": share,
            "sentences": sentences,
        })

    return {"total": _marks(total_weight), "lines": lines, "measures": measures}


def for_course(course):
    """The description for a course, or None if it has no scheme."""
    return describe(getattr(course, "participation_scheme", None))

