"""The words students read come from the settings that govern the behaviour."""

from django.test import SimpleTestCase

from .models import Course
from .selection_policy import describe


def policy(**kwargs):
    kwargs.setdefault("weight_exponent", 0.0)
    kwargs.setdefault("allow_bonus", False)
    return " ".join(describe(Course(**kwargs)))


class PolicyTextTests(SimpleTestCase):
    def test_the_hard_end_does_not_promise_a_second_chance(self):
        text = policy(spoken_weight=0.0)
        self.assertIn("have not spoken yet", text)
        self.assertNotIn("may very well be called again", text)

    def test_the_published_cpsc430_wording(self):
        text = policy(spoken_weight=0.5)
        self.assertIn("twice the chances of being called", text)
        self.assertIn("may very well be called again", text)
        self.assertIn("must be ready to answer", text)

    def test_the_multiplier_follows_the_dial(self):
        self.assertIn("twice", policy(spoken_weight=0.5))
        self.assertIn("four times", policy(spoken_weight=0.25))
        self.assertIn("three times", policy(spoken_weight=1 / 3))

    def test_no_preference_says_so(self):
        text = policy(spoken_weight=1.0)
        self.assertIn("makes no difference", text)
        self.assertNotIn("prioritizes", text)

    def test_the_term_long_bias_is_disclosed_only_when_it_is_on(self):
        # It is real and students are not told about it anywhere else.
        self.assertIn(
            "spoken less are also somewhat more likely",
            policy(spoken_weight=0.5, weight_exponent=1.0),
        )
        self.assertNotIn(
            "across the term",
            policy(spoken_weight=0.5, weight_exponent=0.0).lower(),
        )

    def test_phantom_points_are_described_only_when_awarded(self):
        self.assertIn(
            "keep collecting", policy(spoken_weight=0.5, allow_bonus=True)
        )
        self.assertNotIn(
            "keep collecting", policy(spoken_weight=0.5, allow_bonus=False)
        )

    def test_a_default_course_gets_a_truthful_description(self):
        # Nothing set: the behaviour every course had before the dial existed.
        text = policy(spoken_weight=0.0, weight_exponent=1.0, allow_bonus=True)
        self.assertIn("Once everyone with their hand up has had a turn", text)
        self.assertIn("keep collecting", text)
