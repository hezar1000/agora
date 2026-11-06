"""Hand raises are recorded as events, without disturbing what already works."""

import re
from datetime import date, time, timedelta
from pathlib import Path

import pytz
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import TestCase
from django.utils import timezone

from peer_course import hand_events
from peer_course.models import Course, CourseMember, CourseSession, HandRaise


class HandRaiseRecordingTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Hand Events",
            browsable=True,
            archived=False,
            agora_mode=True,
        )
        self.session = CourseSession.objects.create(
            course=self.course,
            session_date=date(2026, 1, 10),
            start_time_pst=time(10, 0),
            end_time_pst=time(11, 0),
            is_template=False,
        )

    def member(self, username, role="student"):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role=role
        )

    def raise_hand(self, member, list_id=1):
        setattr(member, hand_events.HAND_FIELDS[list_id], True)
        member.save()

    def lower_hand(self, member, list_id=1):
        setattr(member, hand_events.HAND_FIELDS[list_id], False)
        member.save()

    # -- the basic transitions ------------------------------------------

    def test_raising_a_hand_opens_a_row(self):
        student = self.member("raiser")

        self.raise_hand(student)

        raise_row = HandRaise.objects.get(member=student)
        self.assertEqual(raise_row.participation_list, 1)
        self.assertTrue(raise_row.is_open)
        self.assertEqual(raise_row.resolution, "")

    def test_lowering_a_hand_closes_the_same_row(self):
        student = self.member("lowerer")
        self.raise_hand(student)

        self.lower_hand(student)

        raise_row = HandRaise.objects.get(member=student)
        self.assertFalse(raise_row.is_open)
        self.assertEqual(raise_row.resolution, HandRaise.Resolution.LOWERED)
        self.assertEqual(HandRaise.objects.count(), 1)

    def test_raising_again_opens_a_second_row(self):
        student = self.member("repeat")
        self.raise_hand(student)
        self.lower_hand(student)

        self.raise_hand(student)

        self.assertEqual(HandRaise.objects.filter(member=student).count(), 2)
        self.assertEqual(
            HandRaise.objects.filter(member=student, resolved_at__isnull=True).count(),
            1,
        )

    def test_a_redundant_save_does_not_stack_rows(self):
        # Views re-save an unchanged member constantly; every one of those must
        # not look like another raise.
        student = self.member("resaver")
        self.raise_hand(student)

        student.save()
        student.save()

        self.assertEqual(HandRaise.objects.filter(member=student).count(), 1)

    def test_each_list_is_tracked_separately(self):
        student = self.member("multi")

        self.raise_hand(student, 1)
        self.raise_hand(student, 3)

        self.assertEqual(
            sorted(
                HandRaise.objects.filter(member=student).values_list(
                    "participation_list", flat=True
                )
            ),
            [1, 3],
        )

    def test_the_open_session_is_attached(self):
        student = self.member("sessioned")

        self.raise_hand(student)

        self.assertIsNotNone(HandRaise.objects.get(member=student).session_id)

    # -- who counts ------------------------------------------------------

    def test_an_instructors_hand_up_is_not_a_raise(self):
        # On an instructor row `hand_up` means "this queue is open". Recording
        # it would put the instructor in every lecture's participation history.
        instructor = self.member("prof", role="instructor")

        self.raise_hand(instructor)

        self.assertEqual(HandRaise.objects.count(), 0)

    # -- resolutions -----------------------------------------------------

    def test_the_instructor_clearing_is_distinguished_from_lowering(self):
        student = self.member("cleared")
        self.raise_hand(student)

        with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
            self.lower_hand(student)

        self.assertEqual(
            HandRaise.objects.get(member=student).resolution,
            HandRaise.Resolution.CLEARED,
        )

    def test_the_reason_does_not_leak_out_of_its_block(self):
        first = self.member("inside")
        second = self.member("outside")
        self.raise_hand(first)
        self.raise_hand(second)

        with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
            self.lower_hand(first)
        self.lower_hand(second)

        self.assertEqual(
            HandRaise.objects.get(member=second).resolution,
            HandRaise.Resolution.LOWERED,
        )

    def test_record_lowered_closes_a_hand_the_hook_cannot_see(self):
        # `_select_student` lowers the chosen hand with a queryset update, which
        # bypasses signals entirely.
        student = self.member("picked")
        self.raise_hand(student)

        CourseMember.objects.filter(pk=student.pk).update(hand_up=False)
        hand_events.record_lowered(student, 1, HandRaise.Resolution.SPOKE)

        raise_row = HandRaise.objects.get(member=student)
        self.assertFalse(raise_row.is_open)
        self.assertEqual(raise_row.resolution, HandRaise.Resolution.SPOKE)

    def test_record_lowered_keeps_a_later_save_from_double_closing(self):
        student = self.member("claimed")
        self.raise_hand(student)
        CourseMember.objects.filter(pk=student.pk).update(hand_up=False)
        hand_events.record_lowered(student, 1, HandRaise.Resolution.SPOKE)

        student.hand_up = False
        student.save()

        self.assertEqual(
            HandRaise.objects.get(member=student).resolution,
            HandRaise.Resolution.SPOKE,
        )

    # -- safety ----------------------------------------------------------

    def test_a_fixture_load_records_nothing(self):
        # Replaying serialised rows must not invent history that never happened.
        student = self.member("fixture")
        student.hand_up = True
        post_save.send(
            sender=CourseMember,
            instance=student,
            created=False,
            raw=True,
            using="default",
        )

        self.assertEqual(HandRaise.objects.count(), 0)

    def test_recording_failure_cannot_break_a_lecture(self):
        student = self.member("resilient")

        def explode(*args, **kwargs):
            raise RuntimeError("recording is down")

        original = hand_events._open
        hand_events._open = explode
        try:
            with self.assertLogs("peer_course.hand_events", level="ERROR"):
                self.raise_hand(student)
        finally:
            hand_events._open = original

        student.refresh_from_db()
        self.assertTrue(student.hand_up)  # the hand still went up

    def test_clearing_a_course_closes_every_open_hand_event(self):
        student = self.member("lecture-boundary")
        self.raise_hand(student, 1)
        self.raise_hand(student, 3)

        cleared = hand_events.clear_student_hands(self.course)

        self.assertEqual(cleared, 1)
        student.refresh_from_db()
        self.assertFalse(student.hand_up)
        self.assertFalse(student.hand_up_list_3)
        raises = HandRaise.objects.filter(member=student)
        self.assertEqual(raises.count(), 2)
        self.assertFalse(raises.filter(resolved_at__isnull=True).exists())
        self.assertEqual(
            set(raises.values_list("resolution", flat=True)),
            {HandRaise.Resolution.CLEARED},
        )

    def test_clearing_also_closes_a_deactivated_students_open_event(self):
        student = self.member("deactivated")
        self.raise_hand(student, 1)
        student.active = False
        student.save(update_fields=["active"])

        hand_events.clear_student_hands(self.course)

        student.refresh_from_db()
        event = HandRaise.objects.get(member=student)
        self.assertFalse(student.hand_up)
        self.assertEqual(event.resolution, HandRaise.Resolution.CLEARED)


class BulkUpdateGuardTests(TestCase):
    """A queryset update bypasses the hook, so each one must be paired."""

    def test_every_bulk_update_on_a_student_hand_is_paired_with_a_record(self):
        views = Path(__file__).resolve().parent.parent / "peer_home" / "views.py"
        source = views.read_text()
        lines = source.splitlines()

        unpaired, examined = [], 0
        for index, line in enumerate(lines):
            if not re.search(r"\.update\(\s*\*\*\{hand_field", line):
                continue
            examined += 1
            window = "\n".join(lines[index : index + 15])
            if "record_lowered" not in window:
                unpaired.append(index + 1)

        # Without this the guard passes by matching nothing, which is how a
        # renamed variable would quietly disable it.
        self.assertEqual(
            examined,
            1,
            "expected exactly one queryset update lowering a student's hand; "
            "if the selection code was restructured, retarget this guard",
        )
        self.assertEqual(
            unpaired,
            [],
            "a queryset update lowers a student's hand without recording it; "
            "the hand event is silently lost at these lines",
        )

    def test_bulk_updates_on_named_hand_fields_target_instructors_only(self):
        views = Path(__file__).resolve().parent.parent / "peer_home" / "views.py"
        examined = 0
        for index, line in enumerate(views.read_text().splitlines(), start=1):
            if not re.search(r"\.update\(hand_up", line):
                continue
            examined += 1
            with self.subTest(line=index):
                self.assertIn(
                    "all_instructors",
                    line,
                    "a bulk update writes a hand field on something other than "
                    "the instructor queue-open flag, so its event is lost",
                )
        self.assertGreater(examined, 0, "the guard matched nothing; retarget it")


class ScheduleCreatedInAdvanceTests(TestCase):
    """A hand raised must be recorded against the lecture it was raised in.

    Sessions used to be created on the morning they ran, so "the newest unended
    session" and "the lecture happening now" were the same row. A term's
    schedule entered ahead of time breaks that: every later lecture is unended
    too, and the newest is the last one of the term.
    """

    def setUp(self):
        self.course = Course.objects.create(
            displayname="Scheduled ahead", browsable=False, archived=False,
            agora_mode=True, course_timezone="US/Pacific",
        )
        today = timezone.localdate()
        self.past = CourseSession.objects.create(
            course=self.course, is_template=False, name="Lecture 1",
            session_date=today - timedelta(days=2),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        self.future = [
            CourseSession.objects.create(
                course=self.course, is_template=False, name=f"Lecture {n}",
                session_date=today + timedelta(days=7 * n),
                start_time_pst=time(14, 0), end_time_pst=time(15, 30),
            )
            for n in range(1, 16)
        ]
        self.student = CourseMember.objects.create(
            course=self.course,
            user=User.objects.create_user("scheduled-ahead-student"),
            role="student",
        )

    def raise_hand(self):
        member = CourseMember.objects.get(pk=self.student.pk)
        member.hand_up = True
        member.save()
        return HandRaise.objects.filter(member=member).order_by("-id").first()

    def test_a_raise_is_not_recorded_against_a_lecture_still_to_come(self):
        recorded = self.raise_hand()

        self.assertIsNotNone(recorded)
        self.assertNotIn(
            recorded.session_id,
            [session.id for session in self.future],
            "the raise was recorded against a lecture that has not happened",
        )

    def test_it_falls_back_to_the_most_recent_lecture_that_has_run(self):
        recorded = self.raise_hand()

        self.assertEqual(recorded.session_id, self.past.id)

    def test_a_lecture_inside_its_window_still_wins(self):
        now = timezone.localtime(timezone.now(), pytz.timezone("US/Pacific"))
        running = CourseSession.objects.create(
            course=self.course, is_template=False, name="Running now",
            session_date=now.date(),
            start_time_pst=(now - timedelta(minutes=5)).time(),
            end_time_pst=(now + timedelta(hours=1)).time(),
        )

        recorded = self.raise_hand()

        self.assertEqual(recorded.session_id, running.id)
