"""What a student is told about the lecture they are sitting in.

The screen in the room used to say "Participation Points: 3" whatever the
course graded. In a course graded on attendance that number means nothing --
the fact that matters is whether the lecture counts yet -- and in a course
graded on the whole term it invited a student to read a running total as a
grade it was not.
"""

import re
from datetime import date, time
from pathlib import Path

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from peer_course import participation_presets as presets
from peer_course import participation_rules
from peer_course.participation_testing import (
    record_contributions, recorded_credit,
)
from peer_course.models import Course, CourseMember, CourseSession, HandRaise

LECTURE_PAGE = Path(__file__).resolve().parent / "templates" / "agora.html"


class LectureStatusTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="In the room", browsable=True, archived=False,
        )
        self.session = CourseSession.objects.create(
            course=self.course, is_template=False, session_date=date(2026, 9, 9),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        self.student = CourseMember.objects.create(
            course=self.course, role="student",
            user=User.objects.create_user("in-the-room"),
        )

    def raise_hand(self, times=1, participation_list=1):
        record_contributions(
            self.student, self.session, times,
            participation_list=participation_list,
        )

    def status(self):
        self.course.refresh_from_db()
        points, bonus = recorded_credit(self.student, self.session)
        return participation_rules.lecture_status(
            self.course, self.student, self.session, points, bonus
        )

    def test_an_attendance_course_is_told_only_whether_it_counts(self):
        presets.apply_preset(self.course, presets.ATTENDANCE)

        status = self.status()
        self.assertTrue(status["shows_attendance"])
        self.assertFalse(status["counted"])
        self.assertFalse(status["shows_contributions"])
        self.assertFalse(status["shows_points"])

        self.raise_hand()
        self.assertTrue(self.status()["counted"])

    def test_more_raises_do_not_change_an_attendance_course(self):
        presets.apply_preset(self.course, presets.ATTENDANCE)
        self.raise_hand(times=6)

        status = self.status()

        self.assertTrue(status["counted"])
        self.assertFalse(status["shows_contributions"])

    def test_ai_100_is_told_it_counts_and_how_many_times(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.raise_hand(times=3)

        status = self.status()

        self.assertTrue(status["shows_attendance"])
        self.assertTrue(status["counted"])
        self.assertTrue(status["shows_contributions"])
        self.assertEqual(status["contributions"], 3)
        self.assertFalse(status["shows_points"])

    def test_cpsc_430_is_told_this_lecture_s_points(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.raise_hand(times=2)

        status = self.status()

        self.assertTrue(status["shows_points"])
        self.assertEqual(status["points"], 2)
        self.assertFalse(status["shows_attendance"])

    def test_a_raise_in_a_queue_that_does_not_count_is_not_counted(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.included_lists = ["1"]
        self.course.save()
        self.raise_hand(participation_list=2, times=4)

        status = self.status()

        self.assertEqual(status["contributions"], 0)
        self.assertFalse(status["counted"])

    def test_a_course_with_no_scheme_is_told_nothing(self):
        bare = Course.objects.create(
            displayname="No scheme", browsable=True, archived=False,
        )
        member = CourseMember.objects.create(
            course=bare, role="student",
            user=User.objects.create_user("no-scheme"),
        )

        status = participation_rules.lecture_status(bare, member, None)

        self.assertFalse(status["any"])
        self.assertFalse(status["shows_attendance"])


class LecturePageTests(SimpleTestCase):
    def test_the_page_asks_the_flags_and_not_python(self):
        source = LECTURE_PAGE.read_text()
        # `{% if x is not None %}` is Python, not a Django template: the tag
        # cannot parse it, and comparing to an unresolvable `None` makes every
        # branch quietly false. The status carries explicit flags instead.
        self.assertNotIn("is not None", source)
        for flag in ("shows_attendance", "shows_contributions", "shows_points"):
            with self.subTest(flag=flag):
                self.assertIn(f"lecture_status.{flag}", source)

    def test_the_room_is_not_shown_a_term_total(self):
        source = LECTURE_PAGE.read_text()
        self.assertNotIn("Participation Points:", source)
        self.assertNotIn("marks_so_far", source)

    def test_the_phantom_line_follows_the_scheme(self):
        source = LECTURE_PAGE.read_text()
        self.assertNotIn("course.allow_bonus", source)
        self.assertIn("lecture_status.bonus_after_speaking", source)


class LecturePageRendersTests(TestCase):
    """The real page, for each kind of course, read back as a student sees it.

    Unit tests on the flags pass whether or not the template can use them:
    `{% if x is not None %}` computes nothing and renders an empty box. Only
    rendering the page catches that.
    """

    def build(self, name, preset, raises):
        from datetime import time

        now = timezone.localtime(timezone.now())
        course = Course.objects.create(
            displayname=name, browsable=True, archived=False, 
            course_timezone="US/Pacific",
        )
        presets.apply_preset(course, preset)
        # A window that spans the day rather than `now - 10min` to
        # `now + 2h`. That arithmetic wrapped past midnight: run just after
        # 00:00 and it produced a session dated today running 23:5x to
        # 02:0x, which does not contain the present moment, so
        # active_session_for_course returned None and every assertion here
        # read the ungraded-lecture message instead. Ten minutes of
        # failure a night, and it fails on the date rolling over rather
        # than on anything the tests are about.
        session = CourseSession.objects.create(
            course=course, is_template=False, session_date=now.date(),
            start_time_pst=time(0, 1), end_time_pst=time(23, 59),
        )
        user = User.objects.create_user(f"student-{name}")
        member = CourseMember.objects.create(
            course=course, user=user, role="student"
        )
        if raises:
            record_contributions(member, session, raises)
        return course, user

    def standing_text(self, course, user):
        body = self.standing_html(course, user)
        start = body.find('class="lecture-standing"')
        self.assertNotEqual(start, -1, "the lecture standing block is missing")
        block = body[start:body.index("</div>", start)]
        return " ".join(re.sub(r"<[^>]+>", " ", block).split())

    def standing_html(self, course, user):
        from django.test import Client

        client = Client()
        client.force_login(user)
        session = client.session
        session["course_id"] = course.id
        session.save()
        response = self.client_get(client)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def client_get(self, client):
        with self.settings(ALLOWED_HOSTS=["*", "testserver"]):
            return client.get("/")

    def test_an_attendance_course_says_whether_the_lecture_counts(self):
        course, user = self.build("attendance-yes", presets.ATTENDANCE, 1)
        text = self.standing_text(course, user)
        body = self.standing_html(course, user)

        self.assertIn("Attendance counted for this lecture", text)
        self.assertIn('id="attendance_no" hidden', body)
        self.assertNotIn('id="attendance_yes" hidden', body)
        self.assertNotIn("Hand raises counted", text)
        self.assertNotIn("Hand raise points", text)

    def test_it_says_what_to_do_when_the_lecture_does_not_count_yet(self):
        course, user = self.build("attendance-no", presets.ATTENDANCE, 0)
        text = self.standing_text(course, user)
        body = self.standing_html(course, user)

        self.assertIn("Not yet attended", text)
        self.assertIn('id="attendance_yes" hidden', body)
        self.assertNotIn('id="attendance_no" hidden', body)

    def test_ai_100_gets_the_tick_and_the_count(self):
        course, user = self.build("ai100-shape", presets.COVERAGE_AND_VOLUME, 3)
        text = self.standing_text(course, user)

        self.assertIn("Attendance counted for this lecture", text)
        # Three counted raises: the first is attendance, two are participation.
        self.assertIn("Additional hand raises this lecture: 2", text)
        self.assertIn("each time the instructor calls on someone", text)

    def test_a_per_lecture_course_gets_this_lecture_s_points(self):
        course, user = self.build("per-lecture", presets.PER_LECTURE, 2)
        text = self.standing_text(course, user)

        self.assertIn("Hand raise points this lecture:", text)
        # The number renders, rather than an empty space where a context
        # variable the page does not have used to be.
        self.assertRegex(text, r"Hand raise points this lecture:\s*2")
        self.assertIn("each time the instructor calls on someone", text)
        self.assertNotIn("Attendance counted", text)

    def test_no_course_is_shown_a_term_total_in_the_room(self):
        for name, preset in (("t1", presets.ATTENDANCE),
                             ("t2", presets.COVERAGE_AND_VOLUME),
                             ("t3", presets.PER_LECTURE)):
            with self.subTest(preset=preset):
                course, user = self.build(name, preset, 2)
                text = self.standing_text(course, user)
                self.assertNotIn("of 15", text)
                self.assertNotIn("marks", text)


class UngradedLectureTests(TestCase):
    """A lecture that does not count has to say so, in the room.

    AI 100 does not grade its first lecture, "to allow late registration and
    time to set up Agora". Nothing here consulted `counts_for_participation`,
    so a student raising a hand in it was told "this lecture counts for you" --
    the opposite of the truth, on the one day of term it mattered.
    """

    def setUp(self):
        self.course = Course.objects.create(
            displayname="Ungraded first", browsable=True, archived=False,
        )
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()
        self.first = CourseSession.objects.create(
            course=self.course, is_template=False, name="Lecture 1",
            session_date=date(2026, 9, 9),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
            counts_for_participation=False,
        )
        self.second = CourseSession.objects.create(
            course=self.course, is_template=False, name="Lecture 2",
            session_date=date(2026, 9, 11),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        self.student = CourseMember.objects.create(
            course=self.course, role="student",
            user=User.objects.create_user("ungraded-student"),
        )

    def raise_hand(self, session, times=1):
        record_contributions(self.student, session, times)

    def status(self, session):
        self.course.refresh_from_db()
        points, bonus = recorded_credit(self.student, session)
        return participation_rules.lecture_status(
            self.course, self.student, session, points, bonus
        )

    def test_an_ungraded_lecture_says_it_is_ungraded(self):
        self.raise_hand(self.first, times=3)

        status = self.status(self.first)

        self.assertFalse(status["graded_lecture"])
        # And claims nothing about attendance or contributions, because this
        # lecture feeds neither.
        self.assertFalse(status["shows_attendance"])
        self.assertFalse(status["shows_contributions"])

    def test_it_does_not_tell_them_the_lecture_counts(self):
        self.raise_hand(self.first)

        status = self.status(self.first)

        self.assertFalse(
            status["shows_attendance"] and status["counted"],
            "a student was told an ungraded lecture counted for them",
        )

    def test_a_graded_lecture_is_unaffected(self):
        self.raise_hand(self.second, times=2)

        status = self.status(self.second)

        self.assertTrue(status["graded_lecture"])
        self.assertTrue(status["shows_attendance"])
        self.assertTrue(status["counted"])
        self.assertEqual(status["contributions"], 2)

    def test_a_day_with_no_lecture_counts_for_nothing_either(self):
        self.second.cancelled = True
        self.second.save(update_fields=["cancelled"])

        status = self.status(self.second)

        self.assertFalse(status["graded_lecture"])

    def test_the_screen_carries_the_sentence(self):
        source = LECTURE_PAGE.read_text()
        self.assertIn("lecture_status.graded_lecture", source)
        self.assertIn("not counted toward your participation grade", source)


class EveryoneRefreshesWhenSomeoneIsCalledTests(SimpleTestCase):
    """When the instructor calls on a student, every student with a hand up
    has just been credited -- not only the one chosen. The page used to
    refresh only the chosen student's tally and leave the others to a
    45-second fallback timer, so the number a student watched lagged what
    they had earned."""

    def test_the_next_student_broadcast_refreshes_every_student(self):
        source = LECTURE_PAGE.read_text()
        start = source.index("case 'next-student':")
        end = source.index("case 'undo-last-call':", start)
        block = source[start:end]
        # update() outside the "is it me" branch: once, for everyone.
        mine = block[block.index("if (value == currentUsername) {"):]
        mine = mine[: mine.index("}")]
        self.assertNotIn("update();", mine)
        self.assertIn("update();", block)


class AttendanceThenParticipationTests(TestCase):
    """Under AI 100's rubric the first counted raise in a lecture is the
    attendance and every one after it is participation. The room says so."""

    def setUp(self):
        self.course = Course.objects.create(
            displayname="Attendance first", browsable=True, archived=False,
        )
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()
        self.session = CourseSession.objects.create(
            course=self.course, is_template=False, session_date=date(2026, 9, 16),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        self.student = CourseMember.objects.create(
            course=self.course, role="student",
            user=User.objects.create_user("first-is-attendance"),
        )

    def status(self):
        points, bonus = recorded_credit(self.student, self.session)
        return participation_rules.lecture_status(
            self.course, self.student, self.session, points, bonus
        )

    def test_the_first_counted_raise_is_attendance_and_not_participation(self):
        record_contributions(self.student, self.session, 1)
        status = self.status()
        self.assertTrue(status["counted"])
        self.assertTrue(status["excludes_covered"])
        self.assertEqual(status["additional"], 0)

    def test_every_later_raise_is_participation(self):
        record_contributions(self.student, self.session, 4)
        self.assertEqual(self.status()["additional"], 3)

    def test_a_per_lecture_course_does_not_subtract_one(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        record_contributions(self.student, self.session, 4)
        status = self.status()
        self.assertFalse(status["excludes_covered"])
        self.assertEqual(status["additional"], 4)

    def test_the_room_says_attendance_then_additional(self):
        source = LECTURE_PAGE.read_text()
        self.assertIn("Attendance counted for this lecture", source)
        self.assertIn("lecture_status.additional", source)
        self.assertIn("data-excludes-covered", source)
