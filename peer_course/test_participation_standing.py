"""What a student sees about their own participation."""

from datetime import date, time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from peer_course import participation_presets as presets
from peer_course import participation_standing as standing
from peer_course.participation_testing import record_contributions
from peer_course.models import Course, CourseMember, CourseSession, HandRaise
from peer_course.templatetags import participation_charts as charts


class HistogramTests(TestCase):
    def test_an_empty_class_has_no_histogram(self):
        self.assertEqual(standing.histogram([]), [])

    def test_a_narrow_range_gets_one_bucket_per_value(self):
        # Smearing three contributions together with four would hide a
        # difference that is worth a grade.
        self.assertEqual(
            standing.histogram([1, 2, 2, 3]), [(1, 1, 1), (2, 2, 2), (3, 3, 1)]
        )

    def test_a_wide_range_is_bucketed(self):
        buckets = standing.histogram(list(range(0, 100)), buckets=5)
        self.assertEqual(len(buckets), 5)
        self.assertEqual(sum(count for _, _, count in buckets), 100)

    def test_everyone_identical_is_one_bucket(self):
        self.assertEqual(standing.histogram([4, 4, 4]), [(4, 4, 3)])


class StandingTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Standing", browsable=True, archived=False,
            show_participation_grade=True,
        )
        self.sessions = [
            CourseSession.objects.create(
                course=self.course,
                session_date=date(2026, 1, 6) + timedelta(days=7 * week),
                start_time_pst=time(10, 0), end_time_pst=time(11, 0),
                is_template=False,
            )
            for week in range(4)
        ]
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()

    def student(self, username):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role="student"
        )

    def contribute(self, member, session, times=1, **kwargs):
        record_contributions(member, session, times, **kwargs)

    def test_no_scheme_means_no_standing(self):
        bare = Course.objects.create(
            displayname="Bare", browsable=True, archived=False
        )
        member = CourseMember.objects.create(
            course=bare, user=User.objects.create_user("bare"), role="student"
        )
        self.assertIsNone(standing.standing_for(bare, member))

    def test_the_lecture_strip_marks_only_lectures_that_ran(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=2)
        self.contribute(me, self.sessions[2], times=1)

        result = standing.standing_for(self.course, me)

        # Two sessions saw hands; the other two were not lectures.
        self.assertEqual(len(result["lectures"]), 2)
        self.assertTrue(all(cell["took_part"] for cell in result["lectures"]))

    def test_absence_from_a_lecture_that_ran_shows_as_a_gap(self):
        me = self.student("me")
        other = self.student("other")
        self.contribute(me, self.sessions[0], times=1)
        self.contribute(other, self.sessions[1], times=1)

        result = standing.standing_for(self.course, me)

        self.assertEqual([c["took_part"] for c in result["lectures"]], [True, False])

    def test_the_percentile_is_tie_safe(self):
        first = self.student("tied-one")
        second = self.student("tied-two")
        for member in (first, second):
            self.contribute(member, self.sessions[0], times=5)

        self.assertEqual(
            standing.standing_for(self.course, first)["percentile"],
            standing.standing_for(self.course, second)["percentile"],
        )

    def test_the_curve_rises(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=3)

        points = standing.standing_for(self.course, me)["curve"]

        self.assertGreater(len(points), 1)
        grades = [grade for _, grade in points]
        self.assertEqual(grades, sorted(grades))

    def test_the_class_is_shown_only_when_the_grade_depends_on_it(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=1)
        self.assertTrue(standing.standing_for(self.course, me)["shows_class"])

        presets.apply_preset(self.course, presets.ATTENDANCE)
        self.course.refresh_from_db()
        # Attendance is absolute, so there is no reason to show the class at all.
        self.assertFalse(standing.standing_for(self.course, me)["shows_class"])


class SchemeShapesTheViewTests(StandingTests):
    """A picture that does not reflect the grading scheme is worse than none:
    it implies a relationship the grade does not have."""

    def test_a_per_lecture_scheme_shows_lectures_not_a_term_curve(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        me = self.student("me")
        other = self.student("other")
        self.contribute(me, self.sessions[0], times=6)
        self.contribute(other, self.sessions[0], times=2)
        self.contribute(me, self.sessions[1], times=1)
        self.contribute(other, self.sessions[1], times=5)

        result = standing.standing_for(self.course, me)

        self.assertTrue(result["lecture_grades"])
        # The term total is not what this grade depends on.
        self.assertEqual(result["curve"], [])
        self.assertEqual(result["histogram"], [])
        self.assertFalse(result["shows_volume"])

    def test_a_volume_scheme_shows_the_term_curve_and_no_lecture_bars(self):
        presets.apply_preset(self.course, presets.VOLUME)
        self.course.refresh_from_db()
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=4)

        result = standing.standing_for(self.course, me)

        self.assertTrue(result["curve"])
        self.assertEqual(result["lecture_grades"], [])

    def test_an_unweighted_measure_is_not_drawn(self):
        # Configuration left lying around should not become a chart.
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()
        rubric = self.course.participation_scheme.rubrics.get(
            measure="volume"
        )
        rubric.weight = 0
        rubric.save()
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=3)

        result = standing.standing_for(self.course, me)

        self.assertFalse(result["shows_volume"])
        self.assertEqual(result["curve"], [])

    def test_each_lecture_is_graded_against_its_own_class(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        me = self.student("me")
        other = self.student("other")
        # Top of a busy lecture, and top of a quiet one.
        self.contribute(me, self.sessions[0], times=12)
        self.contribute(other, self.sessions[0], times=3)
        self.contribute(me, self.sessions[1], times=2)
        self.contribute(other, self.sessions[1], times=1)

        grades = standing.standing_for(self.course, me)["lecture_grades"]

        # Both are full marks: leading a quiet lecture is not worth less.
        self.assertEqual([entry["grade"] for entry in grades], [1.0, 1.0])

    def test_the_dropped_lectures_are_the_lowest_and_are_marked(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        scheme = self.course.participation_scheme
        scheme.dropped_lectures = 1
        scheme.save()
        self.course.refresh_from_db()
        me = self.student("me")
        other = self.student("other")
        for session in self.sessions[:3]:
            self.contribute(other, session, times=5)
        self.contribute(me, self.sessions[0], times=9)
        self.contribute(me, self.sessions[1], times=1)
        self.contribute(me, self.sessions[2], times=7)

        grades = standing.standing_for(self.course, me)["lecture_grades"]

        dropped = [entry for entry in grades if not entry["counted"]]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["grade"], min(e["grade"] for e in grades))


class GradeIsShownTests(StandingTests):
    """The grade is the thing that matters, so it has to be on the page and it
    has to be checkable."""

    def test_each_part_is_reported_as_its_own_percentage(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        scheme.lectures_expected = 4
        scheme.dropped_lectures = 0
        scheme.save()
        self.course.refresh_from_db()
        me = self.student("me")
        for session in self.sessions:
            self.contribute(me, session, times=1)

        breakdown = standing.standing_for(self.course, me)["breakdown"]

        self.assertEqual(
            [line["measure"] for line in breakdown], ["coverage", "volume"]
        )
        for line in breakdown:
            with self.subTest(measure=line["measure"]):
                self.assertGreaterEqual(line["percent"], 0)
                self.assertLessEqual(line["percent"], 100)

    def test_each_part_is_shown_against_its_configured_marks(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=3)

        result = standing.standing_for(self.course, me)

        for absent in ("grade_percent", "earned", "available"):
            with self.subTest(key=absent):
                self.assertNotIn(absent, result)
        for line in result["breakdown"]:
            self.assertIn("earned", line)
            self.assertIn("available", line)
            for absent in ("weight", "share"):
                with self.subTest(key=absent):
                    self.assertNotIn(absent, line)

    def test_an_unweighted_measure_is_not_in_the_breakdown(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        rubric = self.course.participation_scheme.rubrics.get(measure="coverage")
        rubric.weight = 0
        rubric.save()
        self.course.refresh_from_db()
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=2)

        breakdown = standing.standing_for(self.course, me)["breakdown"]

        self.assertEqual([line["measure"] for line in breakdown], ["volume"])

    def test_a_single_measure_course_reports_one_figure(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        me = self.student("me")
        other = self.student("other")
        self.contribute(me, self.sessions[0], times=8)
        self.contribute(other, self.sessions[0], times=2)

        breakdown = standing.standing_for(self.course, me)["breakdown"]

        self.assertEqual([line["measure"] for line in breakdown], ["per_lecture"])
        self.assertEqual(breakdown[0]["percent"], 100)

    def test_the_page_renders_each_part_in_marks(self):
        source = (
            Path(__file__).resolve().parent
            / "templates"
            / "course-user-participation.html"
        ).read_text()
        self.assertIn("standing.breakdown", source)
        self.assertIn("line.earned", source)
        self.assertIn("line.available", source)
        for gone in ("grade_percent", "standing.earned", "line.share", "line.weight"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, source)

    def test_the_page_keeps_the_schema_and_drops_the_explanatory_essay(self):
        source = (
            Path(__file__).resolve().parent
            / "templates"
            / "course-user-participation.html"
        ).read_text()
        for useful in (
            "figure.target",
            "standing.volume_median",
            "Recorded hand raises",
        ):
            with self.subTest(useful=useful):
                self.assertIn(useful, source)
        for prose in (
            "The height of the line",
            "How many students sit at each level",
            "No names, and no ranking of individuals",
            "a quiet week cannot lower",
        ):
            with self.subTest(prose=prose):
                self.assertNotIn(prose, source)


class ChartTests(TestCase):
    def test_every_chart_describes_itself_for_a_screen_reader(self):
        rendered = [
            charts.participation_curve([(0, 0.0), (10, 1.0)], 5, 4),
            charts.participation_histogram([(0, 2, 3), (3, 5, 7)], 4),
            charts.lecture_strip([{"took_part": True}, {"took_part": False}], 1),
        ]
        for svg in rendered:
            with self.subTest(svg=svg[:40]):
                self.assertIn('role="img"', svg)
                self.assertIn("<title", svg)
                self.assertIn("<desc", svg)
                self.assertIn("aria-labelledby", svg)

    def test_charts_render_nothing_rather_than_breaking_on_no_data(self):
        self.assertEqual(charts.participation_curve([], 0, 0), "")
        self.assertEqual(charts.participation_histogram([], 0), "")
        self.assertEqual(charts.lecture_strip([], 0), "")

    def test_the_histogram_names_no_one(self):
        svg = charts.participation_histogram([(0, 2, 3), (3, 5, 7)], 4)
        # Counts and ranges only: nothing that identifies another student.
        self.assertIn("3 students with", svg)
        self.assertNotIn("rank", svg.lower())

    def test_the_strip_counts_remaining_drops(self):
        svg = charts.lecture_strip(
            [{"took_part": False}, {"took_part": True}, {"took_part": True}],
            dropped=2,
        )
        self.assertIn("1 dropped lecture remaining", svg)

    def test_lecture_bars_mark_the_dropped_ones_without_hiding_them(self):
        svg = charts.lecture_grade_bars([
            {"grade": 0.8, "median_grade": 0.8, "contributions": 3, "counted": True},
            {"grade": 0.4, "median_grade": 0.8, "contributions": 1, "counted": False},
        ])
        # The student took part; it simply is not counted.
        self.assertIn("dropped, so it costs nothing", svg)
        self.assertIn('fill-opacity="0.25"', svg)

    def test_lecture_bars_describe_the_average_for_a_screen_reader(self):
        svg = charts.lecture_grade_bars([
            {"grade": 1.0, "median_grade": 0.8, "contributions": 9, "counted": True},
            {"grade": 0.6, "median_grade": 0.8, "contributions": 2, "counted": True},
        ])
        self.assertIn("average 80 percent", svg)

    def test_a_student_beyond_the_curve_is_still_drawn_on_it(self):
        # Clamped rather than escaping the axes.
        svg = charts.participation_curve([(0, 0.0), (10, 1.0)], 999, 5)
        self.assertIn("you (999)", svg)


class LectureTableTests(StandingTests):
    """The complaint that started this: a student could not find out how many
    hand raises they had been credited with in each class. The charts said
    percentages and the audit table said "raised" and "lowered"."""

    def test_each_lecture_that_ran_has_a_row_with_the_count(self):
        me = self.student("me")
        other = self.student("other")
        self.contribute(me, self.sessions[0], times=3)
        self.contribute(other, self.sessions[0], times=1)
        self.contribute(other, self.sessions[2], times=2)

        rows = standing.standing_for(self.course, me)["lecture_rows"]

        self.assertEqual([row["session_id"] for row in rows],
                         [self.sessions[0].id, self.sessions[2].id])
        self.assertEqual(rows[0]["raised"], 3)
        self.assertTrue(rows[0]["took_part"])
        self.assertEqual(rows[0]["median"], 2)
        self.assertEqual(rows[1]["raised"], 0)
        self.assertFalse(rows[1]["took_part"])
        self.assertIs(rows[1]["session"], rows[1]["session"])
        self.assertEqual(rows[1]["session"].id, self.sessions[2].id)

    def test_speaking_and_credit_after_it_are_shown_separately(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        scheme = self.course.participation_scheme
        scheme.bonus_after_speaking = True
        scheme.save()
        self.course.refresh_from_db()
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=2, spoke=1, bonus=3)

        result = standing.standing_for(self.course, me)
        row = result["lecture_rows"][0]

        self.assertTrue(result["shows_bonus"])
        self.assertEqual(row["raised"], 2)
        self.assertEqual(row["spoke"], 1)
        self.assertEqual(row["bonus"], 3)
        self.assertEqual(row["contributions"], 5)

    def test_a_per_lecture_row_carries_that_lecture_s_grade(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        me = self.student("me")
        other = self.student("other")
        self.contribute(me, self.sessions[0], times=6)
        self.contribute(other, self.sessions[0], times=2)

        row = standing.standing_for(self.course, me)["lecture_rows"][0]

        self.assertEqual(row["percent"], 100)
        self.assertTrue(row["counted"])

    def test_the_latest_lecture_is_the_most_recent_by_date(self):
        me = self.student("me")
        self.contribute(me, self.sessions[3], times=1)
        self.contribute(me, self.sessions[1], times=4)

        latest = standing.standing_for(self.course, me)["latest_lecture"]

        self.assertEqual(latest["session"].id, self.sessions[3].id)

    def test_the_page_lists_the_count_for_every_lecture(self):
        from django.test import Client

        me = self.student("me")
        me.user.set_password("pw")
        me.user.save()
        self.contribute(me, self.sessions[0], times=3)
        self.contribute(me, self.sessions[1], times=1)

        client = Client()
        client.force_login(me.user)
        with self.settings(ALLOWED_HOSTS=["*", "testserver"]):
            response = client.get(f"/course/{self.course.id}/{me.user.id}/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Hand raises counted, lecture by lecture", body)
        self.assertIn(self.sessions[0].display_name, body)
        # The audit table below says it is not the graded count.
        self.assertIn("the count that is graded", body)


class StudentsSeeTheRecordNotAGradeTests(StandingTests):
    """A total out of 15 in the second week reads as a grade and means
    nothing. Students get the record; staff get the arithmetic."""

    def open_as(self, viewer, member):
        from django.test import Client

        client = Client()
        client.force_login(viewer)
        with self.settings(ALLOWED_HOSTS=["*", "testserver"]):
            response = client.get(f"/course/{self.course.id}/{member.user.id}/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_student_sees_no_running_total(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=3)

        body = self.open_as(me.user, me)

        self.assertNotIn("Where this stands", body)
        self.assertNotIn("/ 15", body)
        self.assertIn("3 hand raises counted", body)
        self.assertNotIn("How this becomes a grade", body)

    def test_staff_see_where_the_arithmetic_stands(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=3)
        prof = CourseMember.objects.create(
            course=self.course, role="instructor",
            user=User.objects.create_user("prof"),
        )

        body = self.open_as(prof.user, me)

        self.assertIn("Where this stands", body)
        self.assertIn("staff view", body)

    def test_the_chart_shows_counts_and_nothing_in_the_page_colour(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=2)

        body = self.open_as(me.user, me)

        self.assertIn("participation-chart raise-bars", body)
        self.assertIn("2 hand raises counted", body)
        # The old charts painted in currentColor and became a black slab.
        self.assertNotIn("currentColor", body)


class DistributionTests(StandingTests):
    def test_a_term_rubric_shows_the_whole_class_including_the_silent(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=3)
        for name in ("quiet", "absent"):
            self.student(name)
        self.contribute(CourseMember.objects.get(user__username="quiet"), self.sessions[0], times=1)

        distribution = standing.standing_for(self.course, me)["distribution"]

        self.assertEqual(distribution["students"], 3)
        self.assertEqual(distribution["mine"], 2)  # three raises, one is attendance

    def test_a_per_lecture_rubric_has_no_class_distribution(self):
        # The class is already inside each lecture's median and top ticks.
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        me = self.student("me")
        other = self.student("other")
        self.contribute(me, self.sessions[0], times=9)
        self.contribute(other, self.sessions[0], times=4)

        result = standing.standing_for(self.course, me)

        self.assertIsNone(result["distribution"])
        self.assertEqual(result["headline"][0]["kind"], "per_lecture")
        self.assertEqual(result["headline"][0]["value"], 100)

    def test_the_headline_is_in_the_rubric_s_units(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=3)
        self.contribute(me, self.sessions[1], times=1)

        kinds = {figure["kind"]: figure for figure in standing.standing_for(self.course, me)["headline"]}

        self.assertEqual(kinds["coverage"]["value"], 2)
        self.assertEqual(kinds["coverage"]["target"], 13)
        self.assertEqual(kinds["volume"]["value"], 2)
        self.assertEqual(kinds["volume"]["target"], 28)

    def test_drops_are_marked_only_once_they_mean_something(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        me = self.student("me")
        for session in self.sessions[:2]:
            self.contribute(me, session, times=2)
        result = standing.standing_for(self.course, me)
        self.assertFalse(result["drops_visible"])
        self.assertFalse(any(row["faded"] for row in result["lecture_rows"]))

    def test_the_lecture_chart_carries_the_rubric_s_overlays(self):
        rows = [{"number": 1, "session": None, "session_id": 1, "raised": 3, "bonus": 0,
                 "spoke": 0, "contributions": 3, "took_part": True, "median": 2,
                 "maximum": 5, "grade": 0.9, "percent": 90, "counted": True,
                 "faded": False, "attendance_part": 1}]
        per_lecture = charts.lecture_chart(rows, "per_lecture")
        self.assertIn("90%", per_lecture)
        self.assertIn("class median 2, top 5", per_lecture)
        split = charts.lecture_chart(rows, "split")
        self.assertIn("attendance + 2 additional", split)
        self.assertIn('data-row="lecture-row-1"', split)
        self.assertEqual(charts.lecture_chart([], "plain"), "")

    def test_the_distribution_chart_marks_the_student_and_the_median(self):
        svg = charts.class_distribution([(0, 0, 4), (1, 1, 6), (2, 2, 1)], 2, 1, 2)
        self.assertIn(">you<", svg)
        self.assertIn(">median<", svg)
        self.assertIn(">top<", svg)
        self.assertNotIn("currentColor", svg)


class AttendanceAndParticipationColumnsTests(StandingTests):
    def test_each_row_separates_attendance_from_participation(self):
        me = self.student("me")
        self.contribute(me, self.sessions[0], times=4)
        self.contribute(me, self.sessions[1], times=1)

        rows = standing.standing_for(self.course, me)["lecture_rows"]

        self.assertEqual([r["attendance_part"] for r in rows], [1, 1])
        self.assertEqual([r["additional"] for r in rows], [3, 0])

    def test_a_late_arrival_still_attends_if_counted_once(self):
        # Attendance is "raising your hand at least once in a lecture"; it
        # need not be the first call of the class.
        me = self.student("me")
        other = self.student("other")
        self.contribute(other, self.sessions[0], times=5)
        self.contribute(me, self.sessions[0], times=1)

        row = standing.standing_for(self.course, me)["lecture_rows"][0]

        self.assertTrue(row["took_part"])
        self.assertEqual(row["additional"], 0)
