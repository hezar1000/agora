"""The settings page has to be readable by someone who has not seen it before.

The grading section used to show only the dials: a number labelled "share of
the participation grade" that was neither a share nor a percentage, two curves
written as ``0 -> 0%, 28 -> 70%``, and three dropdowns about interpolation.
Everything was true and nothing said what a student would get.
"""

import json
import re
from datetime import date, time, timedelta
from importlib import import_module
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from peer_course import lecture_calendar
from peer_course import participation_presets as presets
from peer_course import participation_rules
from peer_course import participation_summary
from peer_course.models import Course, CourseMember, CourseSession, HandRaise
from peer_course.views import CourseViews

TEMPLATE = Path(__file__).resolve().parent / "templates" / "course-edit.html"
STYLE = Path(__file__).resolve().parent / "templates" / "blocks" / "lecture-style.html"


class SchemeInWordsTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Readable", browsable=False, archived=False, 
        )
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()

    def test_it_says_what_the_grade_is_out_of(self):
        summary = participation_summary.for_course(self.course)
        self.assertEqual(summary["total"], "15")

    def test_a_weight_is_shown_as_the_share_it_actually_is(self):
        summary = participation_summary.for_course(self.course)
        shares = {m["heading"]: m["share"] for m in summary["measures"]}

        # "5" on its own reads as five percent of the course. It is five parts
        # of fifteen, and the page has to say so.
        self.assertEqual(shares["Attendance"]["phrase"],
                         "5 of 15 — a third of the participation grade")
        self.assertEqual(shares["Contributions"]["phrase"],
                         "10 of 15 — two thirds of the participation grade")

    def test_it_states_the_denominator_the_rubric_uses(self):
        summary = participation_summary.for_course(self.course)
        self.assertTrue(
            any("marked against 13 of them" in line for line in summary["lines"]),
            summary["lines"],
        )

    def test_it_describes_the_contribution_scale_in_marks(self):
        summary = participation_summary.for_course(self.course)
        contributions = next(
            m for m in summary["measures"] if m["heading"] == "Contributions"
        )
        prose = " ".join(contributions["sentences"])
        self.assertIn("0.25 marks up to 28", prose)
        self.assertIn("median student gets 7 of 10", prose)
        self.assertIn("except the one that earned each lecture's attendance", prose)

    def test_changing_a_setting_changes_the_words(self):
        scheme = self.course.participation_scheme
        scheme.dropped_lectures = 0
        scheme.save()
        self.course.refresh_from_db()

        summary = participation_summary.for_course(self.course)
        self.assertTrue(any("none dropped" in line for line in summary["lines"]))

    def test_a_course_without_a_scheme_has_nothing_to_say(self):
        bare = Course.objects.create(
            displayname="Bare", browsable=False, archived=False, 
        )
        self.assertIsNone(participation_summary.for_course(bare))


class CalendarTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Calendar", browsable=False, archived=False,
            course_timezone="US/Pacific",
        )
        CourseSession.objects.create(
            course=self.course, is_template=True, active_days="wed",
            session_date=date(2026, 9, 9), session_end_date=date(2026, 9, 30),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        for day, graded in ((date(2026, 9, 9), False), (date(2026, 9, 16), True),
                            (date(2026, 9, 23), True)):
            CourseSession.objects.create(
                course=self.course, is_template=False,
                session_date=day, start_time_pst=time(14, 0),
                end_time_pst=time(15, 30), counts_for_participation=graded,
            )

    def test_a_day_the_pattern_covers_with_no_lecture_is_called_out(self):
        grid = lecture_calendar.build(self.course, today=date(2026, 9, 1))

        # 30 September is a Wednesday inside the pattern with no lecture on it.
        self.assertIn(date(2026, 9, 30), grid["unplanned"])
        self.assertEqual(grid["counts"][lecture_calendar.PLANNED], 1)

    def test_each_lecture_is_coloured_by_what_it_counts_for(self):
        grid = lecture_calendar.build(self.course, today=date(2026, 9, 1))
        self.assertEqual(grid["counts"][lecture_calendar.LECTURE], 2)
        self.assertEqual(grid["counts"][lecture_calendar.NOT_GRADED], 1)

    def test_a_cancelled_day_is_neither_planned_nor_a_lecture(self):
        CourseSession.objects.filter(session_date=date(2026, 9, 16)).update(
            cancelled=True, counts_for_participation=False
        )

        grid = lecture_calendar.build(self.course, today=date(2026, 9, 1))

        self.assertEqual(grid["counts"][lecture_calendar.NO_LECTURE], 1)
        self.assertEqual(grid["counts"][lecture_calendar.LECTURE], 1)

    def test_a_pattern_reads_as_a_sentence(self):
        sentences = lecture_calendar.pattern_sentences(
            list(self.course.sessions.filter(is_template=True))
        )
        self.assertEqual(
            sentences[0]["text"],
            "Wednesdays, 14:00–15:30, from 9 Sep to 30 Sep 2026",
        )


class SkippingADayTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Skipping", browsable=False, archived=False,
            course_timezone="US/Pacific",
        )
        CourseSession.objects.create(
            course=self.course, is_template=True, active_days="wed",
            session_date=date(2026, 9, 9), session_end_date=date(2026, 12, 2),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        self.instructor = CourseMember.objects.create(
            course=self.course, role="instructor",
            user=User.objects.create_user("skipper"),
        )

    def post(self, payload):
        request = RequestFactory().post(
            f"/course/{self.course.id}/set_lecture_skipped/",
            data=json.dumps(payload), content_type="application/json",
        )
        request.user = self.instructor.user
        store = import_module(settings.SESSION_ENGINE).SessionStore
        request.session = store()
        request.session["course_id"] = self.course.id
        return CourseViews.set_lecture_skipped(request, self.course.id)

    def test_marking_a_holiday_creates_the_row_that_blocks_the_pattern(self):
        response = self.post({"date": "2026-09-30", "skipped": True})

        self.assertEqual(response.status_code, 200)
        session = CourseSession.objects.get(
            course=self.course, session_date=date(2026, 9, 30)
        )
        self.assertTrue(session.cancelled)
        self.assertFalse(session.counts_for_participation)
        # The spawner fills an empty slot and skips a full one, so the row
        # existing is what stops a lecture appearing.
        self.assertEqual(session.start_time_pst, time(14, 0))

    def test_it_can_be_undone(self):
        self.post({"date": "2026-09-30", "skipped": True})
        self.post({"date": "2026-09-30", "skipped": False})

        session = CourseSession.objects.get(session_date=date(2026, 9, 30))
        self.assertFalse(session.cancelled)

    def test_a_lecture_with_hands_raised_is_not_thrown_away(self):
        session = CourseSession.objects.create(
            course=self.course, is_template=False,
            session_date=date(2026, 9, 16), start_time_pst=time(14, 0),
            end_time_pst=time(15, 30),
        )
        student = CourseMember.objects.create(
            course=self.course, role="student",
            user=User.objects.create_user("spoke-up"),
        )
        HandRaise.objects.create(
            member=student, session=session, participation_list=1,
            raised_at=timezone.now(),
        )

        response = self.post({"date": "2026-09-16", "skipped": True})

        self.assertEqual(response.status_code, 409)
        session.refresh_from_db()
        self.assertFalse(session.cancelled)

    def test_a_bad_date_is_refused(self):
        self.assertEqual(self.post({"date": "the 30th", "skipped": True}).status_code, 400)
        self.assertEqual(self.post({"date": "2026-09-30"}).status_code, 400)


class PagePresentationTests(SimpleTestCase):
    def test_the_page_shows_the_summary_before_the_dials(self):
        source = TEMPLATE.read_text()
        self.assertIn("grade-summary", source)
        self.assertLess(
            source.index("grade-summary"), source.index("measure-card"),
            "the dials come before the explanation",
        )

    def test_lectures_are_one_section_rather_than_two(self):
        source = TEMPLATE.read_text()
        self.assertEqual(source.count("settings-legacy-heading\">Lectures</h3>"), 1)
        self.assertNotIn("Weekly schedule</h3>", source)

    def test_the_calendar_legend_names_every_state_the_grid_can_show(self):
        # A day's class is built from its kind, so the legend swatch is the
        # only literal in the template; both need styling or a state renders
        # as an unmarked blank cell.
        page, style = TEMPLATE.read_text(), STYLE.read_text()
        for kind in ("lecture", "not-graded", "no-lecture", "planned"):
            with self.subTest(kind=kind):
                self.assertIn(f"lecture-chip--{kind}", page)
                self.assertIn(f".lecture-chip--{kind}", style)
                self.assertIn(f".lecture-day--{kind}", style)
        for kind in (lecture_calendar.LECTURE, lecture_calendar.NOT_GRADED,
                     lecture_calendar.NO_LECTURE, lecture_calendar.PLANNED):
            with self.subTest(constant=kind):
                self.assertIn(f".lecture-day--{kind}", style)

    def test_the_stylesheet_is_not_the_one_MTA_shares(self):
        # blocks/settings-style.html is byte-identical on both products. These
        # screens exist only here, so their styling cannot live in it.
        self.assertIn("lecture-style.html", TEMPLATE.read_text())
        self.assertIn(".lecture-calendar", STYLE.read_text())

    def test_no_rem_units(self):
        # Bootstrap 3 sets html{font-size:10px}, so a rem is 10px and every
        # rem-based size renders about a third smaller than it reads.
        self.assertEqual(re.findall(r"[0-9.]+rem", STYLE.read_text()), [])

    def test_the_narrow_layout_is_actually_defined(self):
        source = STYLE.read_text()
        self.assertIn("@media (max-width: 767px)", source)
        # The lecture table has four columns; at 320px they have to stack.
        narrow = source[source.index("@media (max-width: 767px)"):]
        self.assertIn(".lecture-table thead { display: none; }", narrow)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", narrow)


class OneParticipationSystemTests(TestCase):
    """There is one answer to "what is a hand raise worth", not two.

    Agora used to answer it twice. The tally students watched during a lecture
    came from four columns on the course; the grade came from four fields on
    the scheme. Nothing kept them in step, so an instructor could raise what a
    hand was worth, watch the lecture screen agree, and never learn the grade
    had not moved -- and a student watched a number climb that was not their
    grade.
    """

    def setUp(self):
        self.course = Course.objects.create(
            displayname="One system", browsable=False, archived=False,
            hand_raise_point=1, speaking_point=0,
        )
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()

    def test_the_lecture_asks_the_scheme_what_a_raise_is_worth(self):
        scheme = self.course.participation_scheme
        scheme.raise_value = 3
        scheme.speak_bonus = 2
        scheme.lecture_cap = 9
        scheme.bonus_after_speaking = True
        scheme.save()
        # The legacy columns say something else entirely.
        self.course.hand_raise_point = 1
        self.course.speaking_point = 0
        self.course.max_hand_raise_points = None
        self.course.allow_bonus = False
        self.course.save()
        self.course.refresh_from_db()

        rules = participation_rules.for_course(self.course)

        self.assertEqual(rules.raise_value, 3)
        self.assertEqual(rules.speak_bonus, 2)
        self.assertEqual(rules.lecture_cap, 9)
        self.assertTrue(rules.bonus_after_speaking)
        self.assertEqual(rules.speaking_total, 5)

    def test_a_course_with_no_scheme_still_has_rules(self):
        bare = Course.objects.create(
            displayname="No scheme", browsable=False, archived=False,
            hand_raise_point=2, speaking_point=1,
            max_hand_raise_points=4, allow_bonus=True,
        )

        rules = participation_rules.for_course(bare)

        self.assertEqual(rules.raise_value, 2)
        self.assertEqual(rules.speak_bonus, 1)
        self.assertEqual(rules.lecture_cap, 4)
        self.assertTrue(rules.bonus_after_speaking)

    def test_the_settings_page_has_one_participation_section(self):
        source = TEMPLATE.read_text()
        self.assertNotIn('id="points"', source)
        self.assertNotIn("Points during a lecture", source)
        self.assertIn('id="participation"', source)

    def test_the_course_form_no_longer_owns_what_a_raise_is_worth(self):
        from peer_course.forms import CourseForm

        fields = CourseForm(instance=self.course).fields
        for name in ("hand_raise_point", "speaking_point",
                     "max_hand_raise_points", "allow_bonus"):
            with self.subTest(field=name):
                self.assertNotIn(name, fields)

    def test_every_credit_setting_is_on_the_scheme_form(self):
        from peer_course.participation_forms import ParticipationSchemeForm

        fields = ParticipationSchemeForm(instance=self.course.participation_scheme).fields
        for name in ("raise_value", "speak_bonus", "lecture_cap",
                     "bonus_after_speaking"):
            with self.subTest(field=name):
                self.assertIn(name, fields)

    def test_who_gets_called_on_is_not_filed_under_points(self):
        from peer_course.forms import CourseForm

        selection = next(
            group for group in CourseForm(instance=self.course).fieldsets()
            if group["id"] == "calling"
        )
        names = [field.name for field in selection["fields"]]
        self.assertIn("weight_exponent", names)
        self.assertIn("spoken_weight", names)


class StudentsSeeTheirGradeTests(TestCase):
    """The number a student watches climb has to be their grade."""

    def setUp(self):
        self.course = Course.objects.create(
            displayname="Running total", browsable=True, archived=False,
            show_participation_grade=True,
        )
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        scheme.lectures_expected = 4
        scheme.dropped_lectures = 1
        scheme.save()
        self.course.refresh_from_db()
        self.sessions = [
            CourseSession.objects.create(
                course=self.course, is_template=False,
                session_date=date(2026, 9, 9) + timedelta(days=7 * n),
                start_time_pst=time(14, 0), end_time_pst=time(15, 30),
            )
            for n in range(4)
        ]
        self.student = CourseMember.objects.create(
            course=self.course, role="student",
            user=User.objects.create_user("watcher"),
        )

    def raise_hand(self, member, session, times=1):
        from peer_course.participation_testing import record_contributions

        record_contributions(member, session, times)

    def test_the_standing_carries_marks_out_of_the_marks_available(self):
        from peer_course import participation_standing as standing

        for session in self.sessions[:3]:
            self.raise_hand(self.student, session, times=2)

        view = standing.standing_for(self.course, self.student)

        self.assertEqual(view["marks_available"], 15)
        self.assertGreater(view["marks_so_far"], 0)
        self.assertLessEqual(view["marks_so_far"], view["marks_available"])

    def test_it_says_when_the_figure_can_still_move(self):
        from peer_course import participation_standing as standing

        self.raise_hand(self.student, self.sessions[0])
        view = standing.standing_for(self.course, self.student)

        # Contributions carry a class-relative curve settled at the end of term.
        self.assertTrue(view["marks_can_still_move"])

    def test_a_scheme_with_no_curve_does_not_claim_it_might_move(self):
        from peer_course import participation_standing as standing

        presets.apply_preset(self.course, presets.ATTENDANCE)
        self.course.refresh_from_db()
        self.raise_hand(self.student, self.sessions[0])

        view = standing.standing_for(self.course, self.student)

        self.assertFalse(view["marks_can_still_move"])

    def test_the_page_shows_the_grade_and_not_a_separate_currency(self):
        source = (
            Path(__file__).resolve().parent / "templates"
            / "course-user-participation.html"
        ).read_text()
        self.assertNotIn("Total participation points", source)
        self.assertIn("standing.marks_so_far", source)
        self.assertIn("marks_can_still_move", source)


class RemovingALectureFromTheCalendarTests(SimpleTestCase):
    def test_a_day_is_removed_by_a_control_on_that_day(self):
        source = TEMPLATE.read_text()
        # Not a checkbox two screens further down: the cross is on the date.
        self.assertIn("lecture-day__act--remove", source)
        self.assertIn("lecture-day__act--restore", source)
        self.assertIn("set_lecture_skipped", source)

    def test_removing_and_restoring_are_both_offered(self):
        source = TEMPLATE.read_text()
        self.assertIn('data-skipped="true"', source)
        self.assertIn('data-skipped="false"', source)

    def test_the_control_is_reachable_without_a_mouse(self):
        source = TEMPLATE.read_text()
        style = STYLE.read_text()
        # A control revealed only on hover cannot be used on a touch screen or
        # found by a keyboard.
        self.assertIn("aria-label=", source)
        self.assertIn(".lecture-day__act:focus", style)
        self.assertIn("opacity: 0.55", style)
