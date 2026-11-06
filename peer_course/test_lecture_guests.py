"""Guests in a real lecture: they take part, never count, and leave with it."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from peer_course import lecture_guests, participation_export, participation_schemes, sandbox, sandbox_guard
from peer_course.models import Course, CourseMember, CourseParticipation, HandRaise
from peer_lecture.models import Lecture


@override_settings(
    GUEST_SESSIONS_ENABLED=True,
    ALLOWED_HOSTS=["*", "testserver"],
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class LectureGuestTests(TestCase):
    def setUp(self):
        # A demonstration class arrives with a lecture running and its queues
        # open; turned into an ordinary course, it is a real lecture in
        # progress without building one by hand.
        self.instructor = User.objects.create_user("guest-host", first_name="Ina")
        self.course = sandbox.create(self.instructor)
        Course.objects.filter(pk=self.course.pk).update(
            is_sandbox=False, join_code="", sandbox_expires_at=None, lecture_guests=True,
        )
        self.course.refresh_from_db()
        self.lecture = Lecture.currentLecture(self.course.id)
        self.real = User.objects.create_user("real-student", first_name="Rae")
        self.real_member = CourseMember.objects.create(
            course=self.course, user=self.real, role="student", active=True,
        )
        self.teacher = Client()
        self.teacher.force_login(self.instructor)
        self.select(self.teacher)

    def select(self, client):
        session = client.session
        session["course_id"] = self.course.id
        session.save()

    def code(self):
        return lecture_guests.code_for(self.lecture)

    def join(self, name="Gus Guest", client=None):
        client = client or Client()
        response = client.post(
            reverse("course:sandbox_join"), {"code": self.code(), "name": name},
        )
        self.assertEqual(response.status_code, 302, response.content[:300])
        return client

    def guest(self):
        return CourseMember.objects.get(course=self.course, role="guest")

    # --- the code -----------------------------------------------------------

    def test_the_instructor_sees_the_code_and_students_do_not(self):
        page = self.teacher.get(reverse("home:agora_dash"))
        self.assertContains(page, self.code())
        self.assertContains(page, "removed when this lecture ends")
        student = Client()
        student.force_login(self.real)
        self.select(student)
        self.assertNotContains(student.get(reverse("home:agora_dash")), "lecture-guests")

    def test_no_code_while_the_course_does_not_allow_guests(self):
        Course.objects.filter(pk=self.course.pk).update(lecture_guests=False)
        page = self.teacher.get(reverse("home:agora_dash"))
        self.assertNotContains(page, "lecture-guests")

    def test_the_code_stops_working_when_the_setting_is_off(self):
        code = self.code()
        Course.objects.filter(pk=self.course.pk).update(lecture_guests=False)
        response = Client().post(reverse("course:sandbox_join"), {"code": code, "name": "Gus"})
        self.assertContains(response, "Nothing is open with that code")
        self.assertFalse(CourseMember.objects.filter(role="guest").exists())

    def test_the_code_is_stable_and_unique(self):
        self.assertEqual(self.code(), lecture_guests.code_for(Lecture.objects.get(pk=self.lecture.pk)))
        demo = sandbox.create(User.objects.create_user("other-host"))
        self.assertNotEqual(demo.join_code, self.code())

    # --- taking part ----------------------------------------------------------

    def test_a_name_and_the_code_put_a_guest_in_the_lecture(self):
        client = self.join()
        guest = self.guest()
        self.assertFalse(guest.user.has_usable_password())
        self.assertTrue(sandbox_guard.is_guest(guest.user), "a guest account must be confined")
        page = client.get(reverse("home:agora_dash"))
        self.assertEqual(page.status_code, 200)

    def test_a_guest_raises_a_hand_and_is_called_on_as_a_guest(self):
        client = self.join("Gus Guest")
        self.assertEqual(client.post(reverse("home:enable")).status_code, 200)
        guest = self.guest()
        self.assertTrue(guest.hand_up)
        response = self.teacher.post(reverse("home:random_student"), {"selection_version": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["student"], "Gus Guest (guest)")

    # --- never counting ---------------------------------------------------------

    def test_a_guest_is_in_no_grade_export_or_roster(self):
        client = self.join()
        client.post(reverse("home:enable"))
        student = Client()
        student.force_login(self.real)
        self.select(student)
        student.post(reverse("home:enable"))
        self.teacher.post(reverse("home:random_student"), {"selection_version": 0})
        guest = self.guest()

        self.assertNotIn(guest, list(participation_export.students_for(self.course)))
        self.assertIn(self.real_member, list(participation_export.students_for(self.course)))
        for tallies in participation_schemes.lecture_tallies(self.course).values():
            self.assertNotIn(guest.pk, tallies)
        scheme = self.course.participation_scheme
        self.assertNotIn(guest.pk, participation_schemes.measure_values(self.course, scheme))

    def test_a_guest_cannot_enrol_anywhere_else(self):
        client = self.join()
        other = Course.objects.create(
            displayname="Elsewhere", browsable=True, archived=False,
            stucode="ELSEWHERE-0123456789abc",
        )
        client.post(reverse("course:enroll"), {"coursecode": other.stucode})
        self.assertFalse(CourseMember.objects.filter(course=other, user=self.guest().user).exists())

    def test_a_roll_call_never_records_a_guest(self):
        from peer_course import attendance
        from peer_course.models import AttendanceRecord

        self.join()
        check = attendance.start(self.course, self.instructor, announced_list=3)
        guest = self.guest()
        guest.hand_up_list_3 = True
        guest.save()
        self.real_member.hand_up_list_3 = True
        self.real_member.save()
        attendance.close(check, self.instructor)
        self.assertFalse(AttendanceRecord.objects.filter(member=guest).exists())
        self.assertTrue(AttendanceRecord.objects.filter(member=self.real_member).exists())
        guest.refresh_from_db()
        self.assertFalse(guest.hand_up_list_3, "a guest's hand must still come down")
        self.assertNotIn(guest, list(attendance.absent_students(check)))

    # --- leaving --------------------------------------------------------------

    def test_ending_the_lecture_deletes_guests_and_what_they_did(self):
        client = self.join()
        client.post(reverse("home:enable"))
        guest = self.guest()
        user_id, member_id = guest.user_id, guest.pk
        Lecture.endLecture(self.lecture.pk, timezone.now())
        self.assertFalse(User.objects.filter(pk=user_id).exists())
        self.assertFalse(CourseMember.objects.filter(pk=member_id).exists())
        self.assertFalse(HandRaise.objects.filter(member_id=member_id).exists())
        self.assertFalse(CourseParticipation.objects.filter(participant_id=member_id).exists())
        # The real student and the instructor are untouched.
        self.assertTrue(CourseMember.objects.filter(pk=self.real_member.pk).exists())

    def test_the_code_is_dead_once_the_lecture_ends(self):
        code = self.code()
        Lecture.endLecture(self.lecture.pk, timezone.now())
        response = Client().post(reverse("course:sandbox_join"), {"code": code, "name": "Late"})
        self.assertContains(response, "Nothing is open with that code")

    def test_a_signed_in_visitor_keeps_their_account(self):
        visitor = User.objects.create_user("visitor", first_name="Vi")
        client = Client()
        client.force_login(visitor)
        page = client.get(reverse("course:sandbox_join"))
        self.assertNotContains(page, 'name="name"')
        self.join(client=client)
        member = self.guest()
        self.assertEqual(member.user, visitor)
        self.assertFalse(member.is_sandbox_guest)
        self.assertFalse(sandbox_guard.is_guest(visitor))
        Lecture.endLecture(self.lecture.pk, timezone.now())
        self.assertTrue(User.objects.filter(pk=visitor.pk).exists())
        self.assertFalse(CourseMember.objects.filter(user=visitor).exists())

    def test_a_member_typing_the_code_is_not_made_a_guest(self):
        client = Client()
        client.force_login(self.real)
        self.join(client=client)
        self.assertFalse(CourseMember.objects.filter(role="guest").exists())
        self.assertEqual(CourseMember.objects.get(user=self.real).role, "student")

    def test_the_sweep_catches_a_lecture_closed_without_save(self):
        self.join()
        user_id = self.guest().user_id
        Lecture.objects.filter(pk=self.lecture.pk).update(end_time=timezone.now())
        self.assertTrue(User.objects.filter(pk=user_id).exists())
        lecture_guests.sweep()
        self.assertFalse(User.objects.filter(pk=user_id).exists())

    def test_the_sweep_removes_a_guest_left_in_a_lecture_open_past_a_day(self):
        self.join()
        user = self.guest().user
        User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(hours=25))
        lecture_guests.sweep()
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_a_full_lecture_turns_guests_away(self):
        from unittest import mock

        with mock.patch.object(lecture_guests, "MAX_GUESTS_PER_LECTURE", 1):
            self.join("First")
            response = Client().post(
                reverse("course:sandbox_join"), {"code": self.code(), "name": "Second"},
            )
        self.assertContains(response, "no room for more guests")
