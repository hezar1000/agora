"""The curve engine, pinned against the two rubrics it has to reproduce."""

import itertools
import statistics

from django.test import SimpleTestCase

from peer_course import participation_curves as curves
from peer_course.participation_curves import Anchor, CurveError

# --- CPSC 430, as it is written in peer_course.participation ---------------
CPSC430_ABSOLUTE = [
    Anchor(0, 0.00), Anchor(1, 0.50), Anchor(2, 0.60),
    Anchor(3, 0.65), Anchor(4, 0.70), Anchor("median", 0.80),
]
CPSC430_RELATIVE = [Anchor("median", 0.80), Anchor("max", 1.00)]

# --- the term-wide scheme AI 100 publishes ---------------------------------
AI100_ABSOLUTE = [Anchor(0, 0.00), Anchor(28, 0.70)]
AI100_RELATIVE = [Anchor("median", 0.70), Anchor("max", 1.00)]


def _legacy_grade(total, median_total, maximum_total):
    """``calculate_participation_grade`` verbatim, as the reference."""
    total = max(0, total or 0)
    if total == 0:
        return 0.0
    if maximum_total and total >= maximum_total:
        return 10.0
    if median_total and total >= median_total:
        if maximum_total > median_total:
            grade = 8 + (2 * (total - median_total) / (maximum_total - median_total))
            return round(min(10.0, grade), 2)
        return 8.0
    if total <= 4:
        return {1: 5.0, 2: 6.0, 3: 6.5, 4: 7.0}.get(total, 0.0)
    if median_total and median_total > 4:
        return round(min(8.0, 7 + ((total - 4) / (median_total - 4))), 2)
    return 7.0


class ReproducesCPSC430Tests(SimpleTestCase):
    """The engine must grade CPSC 430 identically to the code it replaces."""

    def grade(self, total, population):
        absolute = curves.evaluate(
            CPSC430_ABSOLUTE, total, population=population,
            interpolation=curves.BY_VALUE,
        )
        relative = curves.evaluate(
            CPSC430_RELATIVE, total, population=population,
            interpolation=curves.BY_VALUE, applies_below_first=False,
        )
        return round(curves.combine(absolute, relative) * 10, 2)

    def test_identical_across_every_median_maximum_and_total(self):
        mismatches, checked = [], 0
        for median, maximum in itertools.product(range(1, 14), range(1, 21)):
            if maximum < median:
                continue
            population = [median, median, maximum]
            if statistics.median(population) != median:
                continue
            for total in range(0, 25):
                checked += 1
                expected = _legacy_grade(total, median, maximum)
                actual = self.grade(total, population)
                if abs(expected - actual) > 0.005:
                    mismatches.append((total, median, maximum, expected, actual))

        self.assertGreater(checked, 4000, "the sweep stopped covering the space")
        self.assertEqual(mismatches[:10], [], f"{len(mismatches)} disagreements")


class MonotonicityTests(SimpleTestCase):
    """Contributing more must never earn less, whatever the class does."""

    def test_a_class_whose_median_equals_its_maximum(self):
        # Every student contributed once, so `max` sits below the fixed anchor
        # at 2. Without carrying the best reward forward the curve descends and
        # a second contribution scores less than the first.
        population = [1, 1, 1]
        grades = [
            curves.evaluate(CPSC430_ABSOLUTE + CPSC430_RELATIVE, total,
                            population=population, interpolation=curves.BY_VALUE)
            for total in range(0, 8)
        ]
        self.assertEqual(grades, sorted(grades))
        self.assertEqual(grades[2], 1.0)

    def test_monotone_for_every_population_shape(self):
        for median, maximum in itertools.product(range(1, 8), range(1, 12)):
            if maximum < median:
                continue
            population = [median, median, maximum]
            grades = [
                curves.evaluate(CPSC430_ABSOLUTE, total, population=population,
                                interpolation=curves.BY_VALUE)
                for total in range(0, 20)
            ]
            with self.subTest(median=median, maximum=maximum):
                self.assertEqual(grades, sorted(grades))


class ReproducesAI100Tests(SimpleTestCase):
    """The drafted scheme, checked against the arithmetic in its proposal."""

    def volume_points(self, total, population):
        absolute = curves.evaluate(
            AI100_ABSOLUTE, total, population=population,
            interpolation=curves.BY_VALUE,
        )
        relative = curves.evaluate(
            AI100_RELATIVE, total, population=population,
            interpolation=curves.BY_RANK, applies_below_first=False,
        )
        return round(curves.combine(absolute, relative) * 10, 3)

    def test_the_absolute_table(self):
        # "a 0.25% increase to your participation grade for each of your first
        # 28 hand raises", against the 10 marks contributions are worth.
        # Tested on the absolute curve alone: in any real class the relative
        # curve may pay more, and the scheme awards the better of the two.
        for total, expected in ((4, 1.0), (8, 2.0), (12, 3.0),
                                (20, 5.0), (24, 6.0), (28, 7.0)):
            with self.subTest(total=total):
                fraction = curves.evaluate(
                    AI100_ABSOLUTE, total, interpolation=curves.BY_VALUE
                )
                self.assertAlmostEqual(fraction * 10, expected, places=3)

    def test_the_absolute_curve_stops_climbing_at_twenty_eight(self):
        # "If you're below the median, you'll get your score as just
        # described" -- so a 40th contribution pays a below-median student
        # nothing beyond the 28th.
        for total in (28, 40, 400):
            with self.subTest(total=total):
                fraction = curves.evaluate(
                    AI100_ABSOLUTE, total, interpolation=curves.BY_VALUE
                )
                self.assertAlmostEqual(fraction * 10, 7.0, places=3)

    def test_twenty_eight_contributions_never_pays_less_than_seven(self):
        # Whatever the class does, the absolute floor holds.
        for population in ([0] * 200, [1] * 99 + [28], list(range(1, 60))):
            with self.subTest(population=population[:3]):
                self.assertGreaterEqual(self.volume_points(28, population), 7.0)

    def test_an_all_zero_class_has_not_earned_relative_credit(self):
        self.assertEqual(self.volume_points(0, [0] * 46), 0.0)

    def test_the_median_student_gets_seven(self):
        population = [10] * 50 + [40] * 50  # median sits between, at 25
        self.assertAlmostEqual(self.volume_points(25, population), 7.0, places=2)

    def test_the_median_student_gets_seven_in_an_odd_sized_class(self):
        population = list(range(1, 46))
        self.assertAlmostEqual(self.volume_points(23, population), 7.0, places=2)

    def test_a_tied_group_below_the_median_keeps_the_fixed_score(self):
        population = [10] * 50 + [40] * 50
        self.assertAlmostEqual(self.volume_points(10, population), 2.5, places=2)

    def test_the_top_of_the_class_gets_ten(self):
        population = list(range(1, 51))
        self.assertAlmostEqual(self.volume_points(50, population), 10.0, places=2)

    def test_between_median_and_top_is_even_in_rank_space(self):
        population = list(range(1, 101))
        # Three quarters of the way up: 7 + 3 * 0.5
        self.assertAlmostEqual(self.volume_points(75, population), 8.5, places=1)

    def test_each_rank_uses_the_published_formula(self):
        population = list(range(1, 47))
        n = len(population)
        for rank in (24, 31, 45, 46):
            expected = 7 + 3 * (rank - n / 2) / (n - n / 2)
            with self.subTest(rank=rank):
                self.assertAlmostEqual(
                    self.volume_points(rank, population), expected, places=3
                )

    def test_a_low_median_still_pays_the_absolute_table(self):
        # "students get the higher one" -- 28 contributions is worth 7.0 even
        # where that is far above the median.
        population = [1] * 99 + [28]
        self.assertGreaterEqual(self.volume_points(28, population), 7.0)


class RankVersusValueTests(SimpleTestCase):
    def test_one_outlier_squashes_a_value_scale_but_not_a_rank_scale(self):
        population = [10] * 20 + [1000]
        anchors = [Anchor("median", 0.70), Anchor("max", 1.00)]

        by_value = curves.evaluate(anchors, 20, population=population,
                                   interpolation=curves.BY_VALUE,
                                   applies_below_first=False)
        by_rank = curves.evaluate(anchors, 20, population=population,
                                  interpolation=curves.BY_RANK,
                                  applies_below_first=False)

        self.assertLess(by_value, 0.72)   # the outlier flattens everyone below it
        self.assertGreater(by_rank, 0.95)  # by rank they are second in the class

    def test_ties_receive_identical_grades(self):
        population = [5, 5, 5, 5, 9, 9, 12]
        anchors = [Anchor("median", 0.70), Anchor("max", 1.00)]
        grades = {
            curves.evaluate(anchors, 9, population=population,
                            interpolation=curves.BY_RANK,
                            applies_below_first=False)
            for _ in range(4)
        }
        self.assertEqual(len(grades), 1)

    def test_rank_fraction_uses_one_through_n_and_keeps_ties_together(self):
        self.assertEqual(curves.rank_fraction([1, 2, 2, 2, 3], 2), 0.8)
        self.assertEqual(curves.rank_fraction([1, 2, 3, 4], 1), 0.25)


class CurveValidationTests(SimpleTestCase):
    def test_a_reward_outside_zero_to_one_is_refused(self):
        with self.assertRaises(CurveError):
            Anchor(5, 8.0)  # points, not a fraction

    def test_an_unknown_statistic_is_refused(self):
        with self.assertRaises(CurveError):
            Anchor("average", 0.5)

    def test_percentile_anchors_are_understood(self):
        self.assertEqual(curves.quantile_of("p90"), 0.9)

    def test_an_empty_curve_is_refused(self):
        with self.assertRaises(CurveError):
            curves.evaluate([], 3)

    def test_an_unknown_interpolation_is_refused(self):
        with self.assertRaises(CurveError):
            curves.evaluate([Anchor(0, 0.0)], 3, interpolation="vibes")

    def test_a_relative_curve_has_no_opinion_below_its_range(self):
        self.assertIsNone(
            curves.evaluate([Anchor("median", 0.8)], 0, population=[5, 5, 5],
                            applies_below_first=False)
        )
