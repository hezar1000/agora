"""Attendance mode: every list opens, the instructor names one out loud.

A hand in any other list is the guess of someone who could not hear, and is
put in front of the instructor to be called on and settled.
"""

import json
from datetime import timedelta

import pytz
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from peer_lecture.models import Lecture

from . import attendance, hand_events
from .models import (
    AttendanceCheck,
    AttendanceRecord,
    Course,
    CourseMember,
    CourseParticipation,
    CourseSession,
    HandRaise,
)

IN_MEMORY_CHANNELS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
Status = AttendanceRecord.Status
RAISE = {1: "/enable/", 2: "/enable_blue/", 3: "/enable_red/", 4: "/enable_yellow/"}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class AttendanceTestCase(TestCase):
    password = "testpass123"

    def setUp(self):
        self.course = Course.objects.create(
            displayname="AI 100 attendance", browsable=True, archived=False,
            included_lists=["1", "2", "3", "4"],
        )
        self.instructor_user = User.objects.create_user("att-instructor", password=self.password)
        self.instructor = CourseMember.objects.create(
            course=self.course, user=self.instructor_user, role="instructor"
        )
        self.students = []
        for n in range(4):
            user = User.objects.create_user(
                f"att-student-{n}", password=self.password,
                first_name=f"Student{n}", last_name="Test", email=f"s{n}@example.invalid",
            )
            self.students.append(
                CourseMember.objects.create(course=self.course, user=user, role="student")
            )
        tz = pytz.timezone(self.course.course_timezone)
        start = timezone.localtime(timezone.now(), tz) - timedelta(hours=1)
        self.session = CourseSession.objects.create(
            course=self.course, is_template=False,
            session_date=start.date(), start_time_pst=start.time(),
            end_time_pst=(start + timedelta(hours=2)).time(),
        )
        Lecture.create(self.course.id)

    def login(self, member):
        self.client.logout()
        self.client.login(username=member.user.username, password=self.password)
        session = self.client.session
        session["course_id"] = self.course.id
        session.save()

    def raise_hand(self, member, list_id):
        self.login(member)
        response = self.client.post(RAISE[list_id])
        self.assertEqual(response.status_code, 200)

    def open_lists(self):
        self.instructor.refresh_from_db()
        return [
            position for position, field in hand_events.HAND_FIELDS.items()
            if getattr(self.instructor, field)
        ]


class RollCallTests(AttendanceTestCase):
    def test_opening_opens_every_list_and_remembers_what_was_open(self):
        self.instructor.hand_up_list_2 = True
        self.instructor.save()

        check = attendance.start(self.course, self.instructor_user, announced_list=3)

        self.assertEqual(self.open_lists(), [1, 2, 3, 4])
        self.assertEqual(check.session, self.session)
        self.assertEqual(check.lists_open_before, {"1": False, "2": True, "3": False, "4": False})

        attendance.close(check, self.instructor_user)
        self.assertEqual(self.open_lists(), [2])

    def test_a_hand_already_up_is_not_an_answer(self):
        self.instructor.hand_up = True
        self.instructor.save()
        self.raise_hand(self.students[0], 1)

        check = attendance.start(self.course, self.instructor_user, announced_list=1)
        attendance.close(check)

        self.assertFalse(check.records.exists())

    def test_the_right_list_is_present_and_any_other_is_to_be_checked(self):
        check = attendance.start(self.course, self.instructor_user, announced_list=3)
        self.raise_hand(self.students[0], 3)
        self.raise_hand(self.students[1], 1)
        self.raise_hand(self.students[2], 4)
        # students[3] has no hand up.

        attendance.close(check, self.instructor_user)

        status = dict(check.records.values_list("member", "status"))
        self.assertEqual(status, {
            self.students[0].pk: Status.PRESENT,
            self.students[1].pk: Status.UNREVIEWED,
            self.students[2].pk: Status.UNREVIEWED,
        })
        summary = attendance.summary(check)
        self.assertEqual(summary["attended"], 1)
        self.assertEqual(summary["unreviewed"], 2)
        self.assertEqual(summary["no_hand"], 1)
        self.assertEqual(
            list(attendance.absent_students(check)), [self.students[3]]
        )

    def test_every_list_at_once_is_the_guess_not_an_answer(self):
        self.course.allow_multiple_queues = True
        self.course.save()
        check = attendance.start(self.course, self.instructor_user, announced_list=2)
        for list_id in (1, 2, 3, 4):
            self.raise_hand(self.students[0], list_id)

        attendance.close(check)

        record = check.records.get()
        self.assertEqual(record.lists_raised, [1, 2, 3, 4])
        self.assertEqual(record.status, Status.UNREVIEWED)

    def test_closing_clears_hands_and_credits_nobody(self):
        check = attendance.start(self.course, self.instructor_user, announced_list=1)
        self.raise_hand(self.students[0], 1)

        attendance.close(check)

        self.students[0].refresh_from_db()
        self.assertFalse(self.students[0].hand_up)
        self.assertEqual(
            HandRaise.objects.get(member=self.students[0]).resolution,
            HandRaise.Resolution.ATTENDANCE,
        )
        self.assertFalse(CourseParticipation.objects.exists())

    def test_ending_the_lecture_keeps_the_answers(self):
        check = attendance.start(self.course, self.instructor_user, announced_list=2)
        self.raise_hand(self.students[0], 2)

        # Every end-of-lecture path clears hands through here.
        hand_events.clear_student_hands(self.course)

        check.refresh_from_db()
        self.assertFalse(check.is_open)
        self.assertEqual(check.records.get().status, Status.PRESENT)

    def test_it_needs_a_lecture(self):
        Lecture.endLecture(Lecture.currentLecture(self.course.id).pk, timezone.now())
        with self.assertRaises(attendance.AttendanceError):
            attendance.start(self.course, self.instructor_user)

    def test_a_hidden_list_is_never_the_one_named(self):
        self.course.color_config = json.dumps({"hidden_green": True, "hidden_blue": True, "hidden_red": True})
        self.course.save()
        with self.assertRaises(attendance.AttendanceError):
            attendance.start(self.course, self.instructor_user)

        self.course.color_config = json.dumps({"hidden_green": True})
        self.course.save()
        for _ in range(20):
            check = attendance.start(self.course, self.instructor_user)
            self.assertIn(check.announced_list, (2, 3, 4))
            attendance.close(check)

    def test_starting_twice_returns_the_open_roll_call(self):
        first = attendance.start(self.course, self.instructor_user)
        self.assertEqual(attendance.start(self.course, self.instructor_user), first)
        self.assertEqual(AttendanceCheck.objects.count(), 1)


class NothingLeaksToStudentsTests(AttendanceTestCase):
    """From outside the room, where the room's hands went is the answer."""

    def setUp(self):
        super().setUp()
        self.check = attendance.start(self.course, self.instructor_user, announced_list=3)
        self.raise_hand(self.students[1], 3)
        self.raise_hand(self.students[2], 3)

    def test_a_student_with_a_hand_up_cannot_read_the_other_hands(self):
        self.raise_hand(self.students[0], 1)
        data = self.client.get("/count_hands_up/").json()
        self.assertEqual(data["count_total"], [0, 0, 0, 0])
        self.assertEqual(data["red_list_of_unspoken"], [])

    def test_the_instructor_still_can(self):
        self.login(self.instructor)
        data = self.client.get("/count_hands_up/").json()
        self.assertEqual(data["count_total"], [0, 0, 2, 0])

    def test_status_says_a_roll_call_is_open_but_not_which_list(self):
        self.login(self.students[0])
        data = self.client.get("/check_status/").json()
        self.assertTrue(data["attendance_open"])
        self.assertNotIn("announced", json.dumps(data))

    def test_a_student_cannot_read_the_roll_call(self):
        self.login(self.students[0])
        response = self.client.get(f"/course/{self.course.id}/attendance/")
        self.assertEqual(response.status_code, 403)

    def test_a_student_cannot_open_or_close_one(self):
        self.login(self.students[0])
        self.assertEqual(self.client.post(f"/course/{self.course.id}/attendance/close/").status_code, 403)
        self.check.refresh_from_db()
        self.assertTrue(self.check.is_open)


class CallingIsPausedTests(AttendanceTestCase):
    def test_calling_on_someone_is_refused_while_it_is_open(self):
        attendance.start(self.course, self.instructor_user, announced_list=1)
        self.raise_hand(self.students[0], 1)
        self.login(self.instructor)

        response = self.client.post("/random_student/", {"selection_version": 0})

        self.assertEqual(response.status_code, 409)
        self.assertFalse(CourseParticipation.objects.exists())


class InstructorFlowTests(AttendanceTestCase):
    def test_start_close_and_settle_the_wrong_list(self):
        self.login(self.instructor)
        state = self.client.post(f"/course/{self.course.id}/attendance/start/").json()
        self.assertTrue(state["open"])
        announced = state["check"]["announced_list"]
        wrong = 1 if announced != 1 else 2

        self.raise_hand(self.students[0], announced)
        self.raise_hand(self.students[1], wrong)
        self.raise_hand(self.students[2], wrong)

        self.login(self.instructor)
        state = self.client.get(f"/course/{self.course.id}/attendance/").json()
        self.assertEqual(state["check"]["right_list"], 1)
        self.assertEqual(state["check"]["other_lists"], 2)

        state = self.client.post(f"/course/{self.course.id}/attendance/close/").json()
        self.assertFalse(state["open"])
        review = state["last"]["review"]
        self.assertEqual({r["name"] for r in review}, {"Student1 Test", "Student2 Test"})

        records = {r["name"]: r["id"] for r in review}
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        self.client.post(
            f"/course/{self.course.id}/attendance/record/{records['Student1 Test']}/",
            {"status": "confirmed"}, **headers,
        )
        state = self.client.post(
            f"/course/{self.course.id}/attendance/record/{records['Student2 Test']}/",
            {"status": "not_in_class"}, **headers,
        ).json()
        self.assertEqual(state["last"]["summary"]["attended"], 2)
        self.assertEqual(state["last"]["summary"]["not_in_class"], 1)

        page = self.client.get(f"/course/{self.course.id}/lectures/{self.session.id}/")
        self.assertEqual(page.status_code, 200)
        follow_up = page.context["roll_calls"][0]["follow_up"]
        self.assertEqual([r.member for r in follow_up], [self.students[2]])
        self.assertContains(page, "s2@example.invalid")

        csv = self.client.get(
            f"/course/{self.course.id}/lectures/{self.session.id}/attendance.csv"
        ).content.decode()
        self.assertIn("Student2 Test,att-student-2,s2@example.invalid,no,Not in class", csv)
        self.assertIn("Student3 Test,att-student-3,s3@example.invalid,no,No hand raised", csv)

    def test_the_named_list_can_be_corrected_while_open(self):
        attendance.start(self.course, self.instructor_user, announced_list=1)
        self.raise_hand(self.students[0], 4)
        self.login(self.instructor)

        self.client.post(f"/course/{self.course.id}/attendance/announce/", {"list": 4})
        self.client.post(f"/course/{self.course.id}/attendance/close/")

        self.assertEqual(AttendanceRecord.objects.get().status, Status.PRESENT)

    def test_someone_with_no_hand_can_be_marked_present_and_unmarked(self):
        check = attendance.start(self.course, self.instructor_user, announced_list=1)
        attendance.close(check)
        self.login(self.instructor)

        self.client.post(
            f"/course/{self.course.id}/attendance/{check.id}/add/",
            {"member": self.students[3].id},
        )
        record = check.records.get()
        self.assertEqual(record.status, Status.ADDED)
        self.assertEqual(attendance.summary(check)["attended"], 1)

        self.client.post(f"/course/{self.course.id}/attendance/record/{record.id}/remove/")
        self.assertFalse(check.records.exists())

    def test_another_courses_record_is_not_reachable(self):
        check = attendance.start(self.course, self.instructor_user, announced_list=1)
        self.raise_hand(self.students[0], 2)
        attendance.close(check)
        other = Course.objects.create(displayname="Other", browsable=True, archived=False)
        CourseMember.objects.create(course=other, user=self.instructor_user, role="instructor")
        self.login(self.instructor)

        response = self.client.post(
            f"/course/{other.id}/attendance/record/{check.records.get().id}/",
            {"status": "not_in_class"},
        )
        self.assertEqual(response.status_code, 404)

    def test_the_lecture_list_links_every_past_lecture(self):
        attendance.close(attendance.start(self.course, self.instructor_user))
        self.login(self.instructor)
        page = self.client.get(f"/course/{self.course.id}/lectures/")
        self.assertContains(page, f"/course/{self.course.id}/lectures/{self.session.id}/")

    def test_students_cannot_see_lecture_pages(self):
        self.login(self.students[0])
        response = self.client.get(f"/course/{self.course.id}/lectures/{self.session.id}/")
        self.assertEqual(response.status_code, 403)


class LectureScreenTests(AttendanceTestCase):
    def test_only_the_instructor_gets_the_panel(self):
        self.login(self.instructor)
        page = self.client.get("/agora_dash/")
        self.assertContains(page, 'id="attendance-panel"')

        self.login(self.students[0])
        page = self.client.get("/agora_dash/")
        self.assertContains(page, 'id="attendance-banner"')
        self.assertNotContains(page, 'id="attendance-panel"')
        self.assertNotContains(page, "/attendance/")


class RollCallCountsAsAttendanceTests(AttendanceTestCase):
    """Attendance is earned by the roll call, or by a contribution in the lecture."""

    def setUp(self):
        super().setUp()
        from .participation_presets import COVERAGE_AND_VOLUME, apply_preset

        apply_preset(self.course, COVERAGE_AND_VOLUME)
        self.scheme = self.course.participation_scheme
        # Graded against the lectures held, so one lecture is the whole term.
        self.scheme.lectures_expected = None
        self.scheme.dropped_lectures = 0
        self.scheme.save()
        self.course.refresh_from_db()

    def roll_call(self, announced, hands):
        check = attendance.start(self.course, self.instructor_user, announced_list=announced)
        for member, list_id in hands:
            self.raise_hand(member, list_id)
        return attendance.close(check)

    def values(self):
        from . import participation_schemes as schemes

        return schemes.measure_values(self.course, self.course.participation_scheme)

    def test_the_right_list_is_attendance_without_a_single_contribution(self):
        self.roll_call(2, [(self.students[0], 2)])
        values = self.values()
        self.assertEqual(values[self.students[0].pk]["coverage"], 1.0)
        self.assertEqual(values[self.students[1].pk]["coverage"], 0.0)

    def test_not_in_class_earns_nothing_from_the_roll_call(self):
        check = self.roll_call(2, [(self.students[0], 3)])
        attendance.review(check.records.get(), Status.NOT_IN_CLASS, self.instructor_user)
        self.assertEqual(self.values()[self.students[0].pk]["coverage"], 0.0)

    def test_confirmed_in_class_does(self):
        check = self.roll_call(2, [(self.students[0], 3)])
        attendance.review(check.records.get(), Status.CONFIRMED, self.instructor_user)
        self.assertEqual(self.values()[self.students[0].pk]["coverage"], 1.0)

    def test_a_contribution_still_earns_attendance(self):
        from .participation_testing import record_contributions

        check = self.roll_call(2, [(self.students[0], 3)])
        attendance.review(check.records.get(), Status.NOT_IN_CLASS, self.instructor_user)
        record_contributions(self.students[0], self.session, times=1)
        self.assertEqual(self.values()[self.students[0].pk]["coverage"], 1.0)

    def test_the_first_raise_only_pays_for_attendance_the_roll_call_did_not(self):
        from .participation_testing import record_contributions

        self.roll_call(1, [(self.students[0], 1)])
        record_contributions(self.students[0], self.session, times=3)
        record_contributions(self.students[1], self.session, times=3)
        values = self.values()
        # Roll call paid for attendance: all three raises are contributions.
        self.assertEqual(values[self.students[0].pk]["volume"], 3)
        # No roll call answer: the first raise was the attendance.
        self.assertEqual(values[self.students[1].pk]["volume"], 2)
        self.assertEqual(values[self.students[1].pk]["coverage"], 1.0)

    def test_the_student_page_and_exports_agree(self):
        from . import participation_export, participation_standing

        self.roll_call(4, [(self.students[0], 4)])
        standing = participation_standing.standing_for(self.course, self.students[0])
        self.assertEqual(standing["coverage_count"], 1)
        self.assertTrue(standing["lecture_rows"][0]["roll_call"])

        for kind in (participation_export.GRADES, participation_export.LECTURES):
            rows = participation_export.build(self.course, kind)
            header = next(r for r in rows if "Lectures attended" in r)
            index = header.index("Lectures attended")
            mine = next(r for r in rows if self.students[0].user.username in r)
            self.assertEqual(mine[index], 1, kind)

    def test_the_lecture_screen_ticks_attendance_after_the_roll_call(self):
        self.roll_call(2, [(self.students[0], 2)])
        self.login(self.students[0])
        status = self.client.get("/check_status/").json()["lecture_status"]
        self.assertTrue(status["counted"])


class EmptyLecturesTests(AttendanceTestCase):
    """A lecture in which nothing happened is not a real lecture."""

    def test_an_empty_lecture_is_left_out_and_one_with_anything_in_it_is_not(self):
        from .sessions import held_lectures

        # Yesterday's, never opened: the duplicate a double start leaves.
        empty = CourseSession.objects.create(
            course=self.course, is_template=False,
            session_date=self.session.session_date - timedelta(days=1),
            start_time_pst=self.session.start_time_pst, end_time_pst=self.session.end_time_pst,
        )
        sessions = CourseSession.objects.filter(course=self.course, is_template=False)
        self.assertEqual(list(held_lectures(sessions)), [])

        attendance.close(attendance.start(self.course, self.instructor_user))
        self.assertEqual(list(held_lectures(sessions)), [self.session])

        self.login(self.instructor)
        page = self.client.get(f"/course/{self.course.id}/lectures/")
        self.assertNotContains(page, f"/lectures/{empty.id}/")


class LecturePageNumbersTests(AttendanceTestCase):
    def test_everyone_credited_for_a_call_counts_not_only_who_was_chosen(self):
        from .participation_testing import record_contributions

        record_contributions(self.students[0], self.session, times=2, spoke=1)
        record_contributions(self.students[1], self.session, times=3)
        self.login(self.instructor)
        page = self.client.get(f"/course/{self.course.id}/lectures/{self.session.id}/")
        self.assertEqual(page.context["numbers"]["students_credited"], 2)
        self.assertEqual(page.context["numbers"]["called_on"], 1)


class DemoConsoleTests(AttendanceTestCase):
    def test_the_console_no_longer_reloads_the_whole_page(self):
        from pathlib import Path

        source = (Path(__file__).parent / "templates" / "sandbox-console.html").read_text()
        self.assertNotIn("location.reload", source)
