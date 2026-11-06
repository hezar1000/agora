"""How this course chooses who speaks, in words, from the settings themselves.

A policy written by hand goes stale the moment someone moves a dial. These
sentences are derived from the same fields the selection code reads, so what
students are told and what actually happens cannot drift apart. The settings
page shows an instructor exactly this text, which is how they check that the
policy they mean is the policy they have.
"""

# Whole-number multiples read better as words than as "2.0 times".
_MULTIPLES = {2: "twice", 3: "three times", 4: "four times", 5: "five times"}


def _multiple(share):
    """How many times more likely an unspoken student is, phrased."""
    factor = 1.0 / share
    rounded = round(factor)
    if abs(factor - rounded) < 0.01 and rounded in _MULTIPLES:
        return _MULTIPLES[rounded]
    return f"{factor:.1f} times".replace(".0 times", " times")


def describe(course):
    """The policy as a list of sentences, in the order a student should read them."""
    share = float(getattr(course, "spoken_weight", 0.0) or 0.0)
    sentences = []

    if share <= 0:
        sentences.append(
            "Agora calls on students who have not spoken yet during the current "
            "class. Once everyone with their hand up has had a turn, anyone may "
            "be called again."
        )
    elif share >= 1:
        sentences.append(
            "Agora chooses at random from the raised hands. Having already "
            "spoken during the current class makes no difference."
        )
    else:
        sentences.append(
            "Agora prioritizes students who have not spoken yet during the "
            f"current class (they have {_multiple(share)} the chances of being "
            "called). However, students who have already spoken may very well "
            "be called again, and must be ready to answer."
        )

    if float(getattr(course, "weight_exponent", 0.0) or 0.0) > 0:
        sentences.append(
            "Across the term, students who have spoken less are also somewhat "
            "more likely to be called."
        )

    scheme = getattr(course, "participation_scheme", None)
    keeps_earning = (
        scheme.bonus_after_speaking if scheme is not None
        else getattr(course, "allow_bonus", False)
    )
    if keeps_earning:
        sentences.append(
            "Once you have spoken during a class, you keep collecting "
            "participation points as other students speak."
        )

    return sentences
