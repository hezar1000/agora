"""How Agora chooses between the hands that are up.

`Course.spoken_weight` is one dial across the policies courses ask for. It is
the share of a chance a student who has already spoken this class keeps, next
to one who has not:

    0.0  they are held back until everyone else has had a turn
    0.5  half as likely, so the unspoken have twice the chance
    1.0  this class is ignored; every raised hand is equally likely

0 is the default and is exactly what every course did before the dial existed.
"""

import numpy as np
from django.test import SimpleTestCase, TestCase

from peer_course.models import Course, CourseMember
from peer_home.views import HomeViews


class Stub:
    """A pool member: the weighting only ever reads pk and id."""

    def __init__(self, pk):
        self.pk = pk
        self.id = pk


def weights(spoken_weight, pool_size, spoken, exponent=0.0):
    # exponent 0 takes the uniform branch, isolating the within-class dial
    # from the term-long one.
    course = Course(weight_exponent=exponent, spoken_weight=spoken_weight)
    pool = [Stub(i) for i in range(pool_size)]
    return HomeViews._selection_weights(course, pool, spoken_ids=set(spoken))


class TheDialTests(SimpleTestCase):
    def test_zero_holds_back_anyone_who_has_spoken(self):
        # The behaviour every course had before the dial: while an unspoken
        # hand is up, a student who has spoken cannot be chosen.
        w = weights(0.0, 4, spoken=[0, 1])
        self.assertEqual(list(w[:2]), [0.0, 0.0])
        self.assertAlmostEqual(w[2], 0.5)
        self.assertAlmostEqual(w[3], 0.5)

    def test_a_half_gives_the_unspoken_twice_the_chance(self):
        # The CPSC 430 policy: "they have twice the chances of being called",
        # and those who have spoken "may very well be called again".
        w = weights(0.5, 2, spoken=[0])
        self.assertAlmostEqual(w[1] / w[0], 2.0)
        self.assertGreater(w[0], 0.0)

    def test_one_ignores_this_class_entirely(self):
        w = weights(1.0, 4, spoken=[0, 1])
        for value in w:
            self.assertAlmostEqual(value, 0.25)

    def test_the_dial_is_smooth_and_monotonic_between_the_ends(self):
        # Nothing jumps: a student who has spoken gets steadily more likely as
        # the dial opens, from impossible to equal.
        chances = [weights(share, 2, spoken=[0])[0]
                   for share in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)]
        self.assertEqual(chances, sorted(chances))
        self.assertAlmostEqual(chances[0], 0.0)
        self.assertAlmostEqual(chances[-1], 0.5)
        self.assertEqual(len(set(chances)), len(chances), "the dial has a flat spot")

    def test_it_always_produces_a_usable_distribution(self):
        for share in (0.0, 0.5, 1.0):
            for spoken in ([], [0], [0, 1], [0, 1, 2, 3]):
                with self.subTest(share=share, spoken=spoken):
                    w = weights(share, 4, spoken=spoken)
                    self.assertAlmostEqual(w.sum(), 1.0)
                    self.assertTrue((w >= 0).all())

    def test_when_everyone_has_spoken_nobody_is_held_back(self):
        # Nobody can be held back against nobody. This is the case the old
        # `pool = unspoken or raisers` fell through to.
        w = weights(0.0, 3, spoken=[0, 1, 2])
        self.assertAlmostEqual(w.sum(), 1.0)
        for value in w:
            self.assertAlmostEqual(value, 1 / 3)

    def test_an_empty_pool_is_not_an_error(self):
        course = Course(weight_exponent=0.0, spoken_weight=0.5)
        self.assertEqual(len(HomeViews._selection_weights(course, [], set())), 0)


class TermLongInteractionTests(TestCase):
    """The two effects multiply; neither replaces the other."""

    def setUp(self):
        self.course = Course.objects.create(
            displayname="weights", browsable=False, archived=False, on_list=False,
            weight_exponent=1.0, spoken_weight=0.5,
        )

    def _pool(self, n):
        from django.contrib.auth.models import User
        members = []
        for i in range(n):
            user = User.objects.create_user(username=f"weights-{i}")
            members.append(CourseMember.objects.create(
                course=self.course, user=user, role="student", active=True
            ))
        return members

    def test_the_term_long_bias_still_applies_when_everyone_has_spoken_today(self):
        # Falling back must not throw away the term-long weighting, which a
        # fallback to a flat distribution would.
        pool = self._pool(2)
        self.course.spoken_weight = 0.0
        w = HomeViews._selection_weights(
            self.course, pool, spoken_ids={m.pk for m in pool}
        )
        self.assertAlmostEqual(w.sum(), 1.0)
        # With no participation rows both sit at the floor, so this asserts the
        # shape of the fallback rather than a particular split.
        self.assertTrue((w > 0).all())

    def test_today_scales_the_term_long_weights_rather_than_replacing_them(self):
        pool = self._pool(2)
        base = HomeViews._selection_weights(self.course, pool, spoken_ids=set())
        scaled = HomeViews._selection_weights(self.course, pool, spoken_ids={pool[0].pk})
        self.assertAlmostEqual(base[0], base[1])
        self.assertAlmostEqual(scaled[1] / scaled[0], 2.0)


class WhatTheStudentIsToldTests(SimpleTestCase):
    """The message follows the settings; it used to be a fixed sentence."""

    SOURCE = None

    def setUp(self):
        from pathlib import Path
        self.source = (Path(__file__).resolve().parent.parent / "peer_course"
                       / "templates" / "agora.html").read_text()

    def test_the_screen_does_not_hardcode_the_phantom_promise(self):
        # It said "you will collect phantom points automatically" in seven
        # places regardless of whether the course awards them, so a course
        # that turned them off kept promising them.
        self.assertNotIn("phantom points automatically", self.source)
        self.assertIn("spokenNote", self.source)

    def test_the_note_is_decided_on_the_server(self):
        from pathlib import Path
        views = (Path(__file__).resolve().parent / "views.py").read_text()
        self.assertIn('render_dict["spoken_note"]', views)
        self.assertIn("you may still be called again", views)
