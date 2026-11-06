"""Demonstration classes: that they work, and that they stay put.

Agora's previous "one-time session" made an empty course with no lecture in
it, so nothing could be demonstrated, and cleaned up only if someone pressed
Log out. These tests pin the opposite of each of those.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from peer_course import sandbox, sandbox_guard
from peer_course.models import (
    Course, CourseMember, CourseSession, HandRaise, ParticipationScheme,
)
from peer_lecture.models import Lecture


@override_settings(GUEST_SESSIONS_ENABLED=True, ALLOWED_HOSTS=["*", "testserver"])
class SandboxCreationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("demo-owner")

    def test_it_arrives_with_a_lecture_already_running(self):
        # The old one had no session and no Lecture row, so no queue could be
        # open and no hand could go up. Nothing could be demonstrated.
        course = sandbox.create(self.owner)

        session = sandbox.session_of(course)
        self.assertIsNotNone(session, "a demonstration class with no lecture")
        self.assertEqual(session.session_date, timezone.localdate())
        self.assertIsNotNone(Lecture.currentLecture(course.id))

    def test_the_queues_are_open_so_the_buttons_do_something(self):
        # A student may only raise a hand while an instructor's own hand is up,
        # which is what "the queue is open" means here.
        course = sandbox.create(self.owner)
        instructor = course.members.get(role="instructor")

        self.assertTrue(instructor.hand_up)
        self.assertTrue(instructor.hand_up_list_2)

    def test_it_arrives_empty_of_students_on_purpose(self):
        # The people trying it out are the students. A crowd of invented
        # classmates raising hands on a timer looks busy and teaches nobody
        # anything about their own course.
        course = sandbox.create(self.owner)

        self.assertEqual(course.members.filter(role="student").count(), 0)
        self.assertEqual(course.members.count(), 1)

    def test_it_can_grade_what_happens_in_it(self):
        course = sandbox.create(self.owner)
        scheme = getattr(course, "participation_scheme", None)

        self.assertIsNotNone(scheme)
        self.assertTrue(
            scheme.rubrics.filter(measure=ParticipationScheme.COVERAGE).exists()
        )

    def test_the_code_is_short_enough_to_read_to_a_room(self):
        course = sandbox.create(self.owner)

        self.assertEqual(len(course.join_code), 6)
        self.assertTrue(course.join_code.isdigit())
        # The enrolment codes are 24 random characters. Nobody reads those out.
        self.assertNotEqual(course.join_code, course.stucode)

    def test_two_sandboxes_do_not_share_a_code_or_a_name(self):
        first = sandbox.create(self.owner)
        second = sandbox.create(self.owner)

        self.assertNotEqual(first.join_code, second.join_code)
        self.assertNotEqual(first.displayname, second.displayname)

    def test_it_is_not_listed_anywhere_a_real_course_would_be(self):
        course = sandbox.create(self.owner)

        self.assertFalse(course.browsable)
        self.assertTrue(course.is_sandbox)

    def test_a_stranger_sees_nothing_of_it(self):
        course = sandbox.create(self.owner)
        from peer_course.base import CourseBase

        stranger = User.objects.create_user("stranger")
        self.assertEqual(CourseBase.get_courses(stranger), [])
        self.assertIsNone(CourseBase.get_course_member(stranger, course.id))


@override_settings(GUEST_SESSIONS_ENABLED=True)
class ExpiryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("expiry-owner")

    def test_it_goes_away_without_anybody_logging_out(self):
        # The old one deleted its course only if the person pressed Log out.
        # Closing the tab left the account and the course behind for good.
        course = sandbox.create(self.owner)
        course.sandbox_expires_at = timezone.now() - timedelta(minutes=1)
        course.save(update_fields=["sandbox_expires_at"])

        sandbox.expire_due()

        self.assertFalse(Course.objects.filter(pk=course.pk).exists())


    def test_the_owner_keeps_their_own_account(self):
        course = sandbox.create(self.owner)

        sandbox.remove(course)

        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())

    def test_one_that_is_still_running_is_left_alone(self):
        course = sandbox.create(self.owner)

        sandbox.expire_due()

        self.assertTrue(Course.objects.filter(pk=course.pk).exists())

    def test_a_real_course_can_never_be_removed_this_way(self):
        real = Course.objects.create(
            displayname="Real", browsable=True, archived=False, 
        )
        with self.assertRaises(ValueError):
            sandbox.remove(real)
        self.assertTrue(Course.objects.filter(pk=real.pk).exists())

    def test_an_expired_code_opens_nothing(self):
        course = sandbox.create(self.owner)
        code = course.join_code
        course.sandbox_expires_at = timezone.now() - timedelta(seconds=1)
        course.save(update_fields=["sandbox_expires_at"])

        self.assertIsNone(sandbox.for_code(code))

    def test_a_code_that_is_not_six_digits_opens_nothing(self):
        sandbox.create(self.owner)
        for attempt in ("", "12345", "1234567", "abcdef", None):
            with self.subTest(code=attempt):
                self.assertIsNone(sandbox.for_code(attempt))


@override_settings(GUEST_SESSIONS_ENABLED=True, ALLOWED_HOSTS=["*", "testserver"])
class JoiningTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("join-owner")
        self.course = sandbox.create(self.owner)

    def join(self, **payload):
        return Client().post(reverse("course:sandbox_join"), payload)

    def test_a_code_and_a_name_is_enough(self):
        response = self.join(code=self.course.join_code, name="Sam Visitor")

        self.assertEqual(response.status_code, 302)
        guest = CourseMember.objects.get(course=self.course, is_sandbox_guest=True)
        self.assertEqual(guest.user.first_name, "Sam")
        self.assertFalse(guest.user.has_usable_password())

    def test_a_wrong_code_joins_nothing(self):
        response = self.join(code="000000", name="Nobody")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            CourseMember.objects.filter(is_sandbox_guest=True).exists()
        )

    def test_a_name_is_required_so_the_instructor_knows_who_spoke(self):
        response = self.join(code=self.course.join_code, name="   ")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            CourseMember.objects.filter(is_sandbox_guest=True).exists()
        )

    def test_joining_is_refused_when_the_feature_is_off(self):
        with override_settings(GUEST_SESSIONS_ENABLED=False):
            response = self.join(code=self.course.join_code, name="Sam")
        self.assertEqual(response.status_code, 404)


@override_settings(GUEST_SESSIONS_ENABLED=True, ALLOWED_HOSTS=["*", "testserver"])
class GuestsStayPutTests(TestCase):
    """A code and a name logs in an account nobody vouched for.

    Two doors turn a non-member into a member of somewhere they were not
    invited: enrolling, and creating a course -- which OPEN_COURSE_CREATION
    leaves open to any signed-in account.
    """

    def setUp(self):
        self.owner = User.objects.create_user("guard-owner")
        self.course = sandbox.create(self.owner)
        self.real = Course.objects.create(
            displayname="A real course", browsable=True, archived=False,
            stucode="REALCODE-0123456789abcd",
        )
        self.client = Client()
        self.client.post(
            reverse("course:sandbox_join"),
            {"code": self.course.join_code, "name": "Guest Person"},
        )
        self.guest = CourseMember.objects.get(
            course=self.course, is_sandbox_guest=True
        ).user

    def test_they_are_recognised_as_a_guest(self):
        self.assertTrue(sandbox_guard.is_guest(self.guest))
        self.assertFalse(sandbox_guard.is_guest(self.owner))
        self.assertEqual(
            sandbox_guard.guest_course_id(self.guest), self.course.id
        )

    def test_they_cannot_enrol_in_a_real_course(self):
        self.client.post(reverse("course:enroll"), {"coursecode": self.real.stucode})

        self.assertFalse(
            CourseMember.objects.filter(course=self.real, user=self.guest).exists()
        )

    def test_they_cannot_create_a_course(self):
        with override_settings(OPEN_COURSE_CREATION=True):
            response = self.client.post(
                reverse("course:create"), {"cname": "Guest's own course"}
            )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Course.objects.filter(displayname="Guest's own course").exists())

    def test_they_are_a_member_of_exactly_one_course(self):
        from peer_course.base import CourseBase

        self.assertEqual(
            [course.pk for course in CourseBase.get_courses(self.guest)],
            [self.course.pk],
        )
        self.assertIsNone(CourseBase.get_course_member(self.guest, self.real.id))

    def test_their_account_goes_when_the_class_does(self):
        sandbox.remove(self.course)

        self.assertFalse(User.objects.filter(pk=self.guest.pk).exists())


@override_settings(GUEST_SESSIONS_ENABLED=True, ALLOWED_HOSTS=["*", "testserver"])
class ConsoleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("console-owner")
        self.client = Client()
        self.client.force_login(self.owner)

    def test_one_action_produces_a_class_and_lands_on_its_console(self):
        response = self.client.post(reverse("course:sandbox_start"))

        self.assertEqual(response.status_code, 302)
        course = Course.objects.get(is_sandbox=True)
        self.assertIn(str(course.id), response["Location"])

    def test_the_console_shows_the_code_and_the_lecture(self):
        self.client.post(reverse("course:sandbox_start"))
        course = Course.objects.get(is_sandbox=True)

        response = self.client.get(
            reverse("course:sandbox_console", kwargs={"cid": course.id})
        )
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # The code, big, because it gets read to a room.
        self.assertIn(course.join_code, body)
        # The instructor's own lecture screen, framed.
        self.assertIn('src="/"', body)
        # And who has a hand up, which is the thing being demonstrated.
        self.assertIn("Hands up now", body)

    def test_somebody_else_cannot_open_the_console(self):
        self.client.post(reverse("course:sandbox_start"))
        course = Course.objects.get(is_sandbox=True)

        other = Client()
        other.force_login(User.objects.create_user("someone-else"))
        response = other.get(
            reverse("course:sandbox_console", kwargs={"cid": course.id})
        )

        self.assertEqual(response.status_code, 404)

    def test_ending_it_deletes_everything(self):
        self.client.post(reverse("course:sandbox_start"))
        course = Course.objects.get(is_sandbox=True)

        self.client.post(reverse("course:sandbox_end", kwargs={"cid": course.id}))

        self.assertFalse(Course.objects.filter(pk=course.pk).exists())
        self.assertFalse(CourseMember.objects.filter(course_id=course.pk).exists())

    def test_a_real_course_cannot_be_ended_through_this_route(self):
        real = Course.objects.create(
            displayname="Real", browsable=True, archived=False, 
        )
        CourseMember.objects.create(course=real, user=self.owner, role="instructor")

        response = self.client.post(
            reverse("course:sandbox_end", kwargs={"cid": real.id})
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Course.objects.filter(pk=real.pk).exists())

    def test_the_whole_feature_is_absent_when_switched_off(self):
        with override_settings(GUEST_SESSIONS_ENABLED=False):
            for name in ("course:sandbox_start", "course:sandbox_join"):
                with self.subTest(view=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 404)




# Raising a hand broadcasts over Channels, so the tests need a channel layer
# that is not the deployment's authenticated Redis.
@override_settings(
    GUEST_SESSIONS_ENABLED=True,
    ALLOWED_HOSTS=["*", "testserver"],
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    },
)
class ItActuallyWorksTests(TestCase):
    """The whole thing, driven through the real URLs, as two browsers.

    The point of these: the previous one-time session looked finished and did
    nothing, because the course it made had no lecture in it and no queue could
    open. Asserting that a hand raised by somebody who joined with a code lands
    in the lecture the instructor is running is the only claim worth making.
    """

    def setUp(self):
        self.instructor = User.objects.create_user("works-instructor")
        self.teacher = Client()
        self.teacher.force_login(self.instructor)

    def start_a_class(self):
        """Exactly what an instructor does: one button, then the console."""
        self.teacher.post(reverse("course:sandbox_start"))
        course = Course.objects.get(is_sandbox=True)
        # Opening the console is what selects the course for this browser,
        # which every course-scoped view then reads from the session.
        self.teacher.get(
            reverse("course:sandbox_console", kwargs={"cid": course.id})
        )
        return course

    def join(self, course, name):
        """A second browser: no account, no login, just the code and a name."""
        student = Client()
        response = student.post(
            reverse("course:sandbox_join"),
            {"code": course.join_code, "name": name},
        )
        self.assertEqual(response.status_code, 302, "joining did not get in")
        return student

    def test_a_joiner_can_raise_a_hand_and_it_lands_in_the_lecture(self):
        course = self.start_a_class()
        student = self.join(course, "Ada Visitor")

        response = student.post(reverse("home:enable"))

        self.assertEqual(response.status_code, 200)
        member = CourseMember.objects.get(course=course, is_sandbox_guest=True)
        member.refresh_from_db()
        self.assertTrue(member.hand_up, "the hand did not go up")

        # And it was recorded against the lecture that is running, not against
        # nothing -- which is what an empty course would have done.
        raise_row = HandRaise.objects.get(member=member)
        self.assertIsNotNone(
            raise_row.session, "the raise was recorded against no lecture"
        )
        self.assertEqual(raise_row.session_id, sandbox.session_of(course).id)

    def test_the_instructor_can_call_on_somebody_who_raised(self):
        course = self.start_a_class()
        for name in ("Ada Visitor", "Bo Visitor", "Cy Visitor"):
            self.join(course, name).post(reverse("home:enable"))

        # `selection_version` is the page's optimistic-concurrency token, so
        # two instructors cannot both call on somebody from a stale screen.
        # Nothing has been selected yet, so it is zero.
        response = self.teacher.post(
            reverse("home:random_student"), {"selection_version": 0}
        )

        self.assertEqual(response.status_code, 200)
        chosen = response.json()
        self.assertNotEqual(chosen.get("student_id"), "None", chosen)
        # Whoever was chosen has their hand down again and is recorded as
        # having spoken, which is the whole mechanism.
        spoke = HandRaise.objects.filter(
            member__course=course, resolution=HandRaise.Resolution.SPOKE
        )
        self.assertEqual(spoke.count(), 1)

    def test_two_joiners_are_different_people(self):
        course = self.start_a_class()
        self.join(course, "Ada Visitor")
        self.join(course, "Bo Visitor")

        members = CourseMember.objects.filter(course=course, is_sandbox_guest=True)
        self.assertEqual(members.count(), 2)
        self.assertEqual(
            len({member.user_id for member in members}), 2,
            "both browsers became the same person",
        )

    def test_the_console_lists_who_has_a_hand_up(self):
        course = self.start_a_class()
        self.join(course, "Ada Visitor").post(reverse("home:enable"))

        body = self.teacher.get(
            reverse("course:sandbox_console", kwargs={"cid": course.id})
        ).content.decode()

        self.assertIn("Ada Visitor", body)
        self.assertIn("Hands up now", body)

    def test_the_lecture_is_open_the_moment_the_class_is_made(self):
        from peer_course.sessions import active_session_for_course

        course = self.start_a_class()

        # Not a scheduled lecture waiting for its start time: one that is
        # running now, which is what the old temp session never had.
        self.assertIsNotNone(active_session_for_course(course))
        self.assertIsNotNone(Lecture.currentLecture(course.id))

    def test_a_joiner_sees_the_lecture_rather_than_an_enrolment_page(self):
        course = self.start_a_class()
        student = self.join(course, "Ada Visitor")

        response = student.get("/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The old flow sent a temp student to /course/enroll/ with a
        # 24-character code to be told out loud.
        self.assertNotIn("coursecode", body)
        self.assertIn("hand", body.lower())

    def test_everything_goes_when_the_class_ends(self):
        course = self.start_a_class()
        self.join(course, "Ada Visitor").post(reverse("home:enable"))
        guest_ids = list(
            CourseMember.objects.filter(course=course, is_sandbox_guest=True)
            .values_list("user_id", flat=True)
        )

        self.teacher.post(reverse("course:sandbox_end", kwargs={"cid": course.id}))

        self.assertFalse(Course.objects.filter(pk=course.pk).exists())
        self.assertFalse(User.objects.filter(pk__in=guest_ids).exists())
        self.assertFalse(HandRaise.objects.filter(member__course=course).exists())
        # The instructor is still themselves.
        self.assertTrue(User.objects.filter(pk=self.instructor.pk).exists())


@override_settings(GUEST_SESSIONS_ENABLED=True, ALLOWED_HOSTS=["*", "testserver"])
class FirstTimeReaderTests(TestCase):
    """What the pages say to somebody who has never seen Agora."""

    def setUp(self):
        self.owner = User.objects.create_user("first-timer")
        self.client = Client()
        self.client.force_login(self.owner)

    def test_the_start_page_does_not_offer_a_code_before_one_exists(self):
        body = self.client.get(reverse("course:sandbox_start")).content.decode()

        # It used to say "join with the six-digit code" on a page where no
        # class, and so no code, existed yet.
        self.assertNotIn("/course/demo/join/", body)
        self.assertIn("What happens when you press it", body)

    def test_the_start_page_says_what_the_button_will_do(self):
        body = self.client.get(reverse("course:sandbox_start")).content.decode()

        for promise in ("lecture already running", "six-digit code",
                        "deletes itself"):
            with self.subTest(promise=promise):
                self.assertIn(promise, body.lower())

    def test_the_console_gives_the_join_link_once_not_twice(self):
        self.client.post(reverse("course:sandbox_start"))
        course = Course.objects.get(is_sandbox=True)

        body = self.client.get(
            reverse("course:sandbox_console", kwargs={"cid": course.id})
        ).content.decode()

        # Once, at the size you would project it.
        self.assertEqual(body.count("/course/demo/join/"), 1)
        self.assertIn("Trying it on your own", body)

    def test_the_console_shows_the_code_and_when_it_expires(self):
        self.client.post(reverse("course:sandbox_start"))
        course = Course.objects.get(is_sandbox=True)

        body = self.client.get(
            reverse("course:sandbox_console", kwargs={"cid": course.id})
        ).content.decode()

        self.assertIn(course.join_code, body)
        self.assertIn("Deletes itself at", body)
        self.assertIn("End now", body)


@override_settings(GUEST_SESSIONS_ENABLED=True, ALLOWED_HOSTS=["*", "testserver"])
class ReachabilityTests(TestCase):
    """A feature nothing links to does not exist.

    /course/demo/ worked from the day it was written and was reachable only by
    typing the URL, which is not a flow anybody would find.
    """

    def _list_page(self, user):
        client = Client()
        client.force_login(user)
        return client.get(reverse("course:list")).content.decode()

    def test_a_new_account_is_offered_the_demonstration(self):
        # The person Agora is being sold to: signed in, in no course at all.
        body = self._list_page(User.objects.create_user("newcomer"))

        self.assertIn(reverse("course:sandbox_start"), body)
        self.assertIn("Try Agora", body)

    def test_an_instructor_with_courses_is_offered_it_too(self):
        instructor = User.objects.create_user("has-a-course")
        course = Course.objects.create(
            displayname="CPSC 999 2026W1", browsable=True, archived=False,
        )
        CourseMember.objects.create(
            user=instructor, course=course, role="instructor", active=True
        )

        body = self._list_page(instructor)

        self.assertIn(reverse("course:sandbox_start"), body)

    def test_a_superuser_is_offered_it_too(self):
        # Its own branch of the template, so its own assertion.
        body = self._list_page(
            User.objects.create_superuser("root", "root@example.com", "x")
        )

        self.assertIn(reverse("course:sandbox_start"), body)

    def test_the_offer_disappears_when_the_feature_is_off(self):
        user = User.objects.create_user("no-demos-here")

        with override_settings(GUEST_SESSIONS_ENABLED=False):
            body = self._list_page(user)

        self.assertNotIn(reverse("course:sandbox_start"), body)
        self.assertNotIn("Try Agora", body)

    def test_the_link_the_page_offers_actually_works(self):
        # Both halves of the flow, not just the anchor: the href resolves, and
        # its page carries the button that makes the class.
        user = User.objects.create_user("follows-the-link")
        client = Client()
        client.force_login(user)

        start = client.get(reverse("course:sandbox_start"))
        self.assertEqual(start.status_code, 200)

        made = client.post(reverse("course:sandbox_start"))
        self.assertEqual(made.status_code, 302)
        self.assertTrue(Course.objects.filter(is_sandbox=True).exists())

    def test_both_doors_are_on_the_login_page(self):
        # Changed 2026-10-06 at the instructor's request: someone trying Agora
        # in a meeting should not need an account to start or join a demo.
        client = Client()
        body = client.get(reverse("account:login")).content.decode()

        self.assertIn(reverse("course:sandbox_join"), body)
        self.assertIn(reverse("course:sandbox_start"), body)
        # Below CWL, not instead of it.
        self.assertLess(body.index("Log in with CWL"), body.index(reverse("course:sandbox_start")))

    def test_neither_door_is_on_the_login_page_when_switched_off(self):
        with override_settings(GUEST_SESSIONS_ENABLED=False):
            body = Client().get(reverse("account:login")).content.decode()
        self.assertNotIn(reverse("course:sandbox_join"), body)
        self.assertNotIn(reverse("course:sandbox_start"), body)


@override_settings(
    GUEST_SESSIONS_ENABLED=True,
    ALLOWED_HOSTS=["*", "testserver"],
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class WithoutAnAccountTests(TestCase):
    """Starting a class from the sign-in page, as nobody in particular."""

    def start(self, name="Ada Lovelace"):
        client = Client()
        response = client.post(reverse("course:sandbox_start"), {"name": name})
        course = Course.objects.filter(is_sandbox=True).order_by("-id").first()
        return client, response, course

    def test_the_start_page_asks_a_stranger_only_for_an_optional_name(self):
        response = Client().get(reverse("course:sandbox_start"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="name"')
        self.assertContains(response, "No account needed")

    def test_starting_makes_a_temporary_instructor_and_lands_on_the_console(self):
        client, response, course = self.start()
        self.assertRedirects(
            response, reverse("course:sandbox_console", kwargs={"cid": course.id}),
            fetch_redirect_response=False,
        )
        host = course.members.get(role="instructor")
        self.assertTrue(host.is_sandbox_guest)
        self.assertEqual(host.user.get_full_name(), "Ada Lovelace")
        self.assertFalse(host.user.has_usable_password())
        self.assertContains(client.get(response["Location"]), course.join_code)

    def test_a_blank_name_still_works(self):
        _client, _response, course = self.start(name="")
        self.assertEqual(course.members.get(role="instructor").user.first_name, "Demo")

    def test_the_temporary_instructor_is_confined_like_a_guest(self):
        _client, _response, course = self.start()
        host = course.members.get(role="instructor").user
        self.assertTrue(sandbox_guard.is_guest(host))
        self.assertEqual(sandbox_guard.guest_course_id(host), course.id)

    def test_the_temporary_instructor_goes_with_the_class(self):
        _client, _response, course = self.start()
        host_id = course.members.get(role="instructor").user_id
        sandbox.remove(course)
        self.assertFalse(User.objects.filter(pk=host_id).exists())

    def test_a_stranger_and_a_stranger_can_run_a_whole_lecture(self):
        teacher, _response, course = self.start()
        student = Client()
        joined = student.post(
            reverse("course:sandbox_join"),
            {"code": course.join_code, "name": "Grace Hopper"},
        )
        self.assertEqual(joined.status_code, 302)
        self.assertEqual(course.members.filter(role="student", is_sandbox_guest=True).count(), 1)
        self.assertEqual(student.get("/").status_code, 200)

    def test_too_many_live_classes_refuses_another(self):
        self.start()
        with mock.patch.object(sandbox, "MAX_LIVE_GUEST_CLASSES", 1):
            _client, response, _course = self.start()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many demonstration classes")
        self.assertEqual(Course.objects.filter(is_sandbox=True).count(), 1)

    def test_a_signed_in_instructor_still_owns_it_as_themselves(self):
        owner = User.objects.create_user("real-owner")
        client = Client()
        client.force_login(owner)
        client.post(reverse("course:sandbox_start"))
        course = Course.objects.get(is_sandbox=True)
        member = course.members.get(role="instructor")
        self.assertEqual(member.user, owner)
        self.assertFalse(member.is_sandbox_guest)


@override_settings(
    GUEST_SESSIONS_ENABLED=True,
    ALLOWED_HOSTS=["*", "testserver"],
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class DemoMicrophoneTests(TestCase):
    """A demo's owner has no settings page; the console holds the switch."""

    def setUp(self):
        self.owner = User.objects.create_user("mic-demo-owner")
        self.client.force_login(self.owner)
        self.client.post(reverse("course:sandbox_start"))
        self.course = Course.objects.get(is_sandbox=True)
        self.console = reverse("course:sandbox_console", kwargs={"cid": self.course.id})
        self.switch = reverse("course:sandbox_microphone", kwargs={"cid": self.course.id})

    def test_a_demo_starts_with_the_microphone_on(self):
        self.assertTrue(self.course.classroom_microphone)
        self.assertContains(self.client.get(self.console), "Phone microphone: <strong>on</strong>")

    def test_the_console_switches_it_off_and_on(self):
        self.client.post(self.switch, {"on": "0"})
        self.course.refresh_from_db()
        self.assertFalse(self.course.classroom_microphone)
        self.assertContains(self.client.get(self.console), "Phone microphone: <strong>off</strong>")
        self.client.post(self.switch, {"on": "1"})
        self.course.refresh_from_db()
        self.assertTrue(self.course.classroom_microphone)

    def test_nobody_else_can_flip_it(self):
        stranger = Client()
        stranger.force_login(User.objects.create_user("mic-stranger"))
        self.assertEqual(stranger.post(self.switch, {"on": "0"}).status_code, 404)
        self.course.refresh_from_db()
        self.assertTrue(self.course.classroom_microphone)

    def test_a_guest_instructor_can_flip_it(self):
        guest = Client()
        guest.post(reverse("course:sandbox_start"), {"name": "Pat"})
        course = Course.objects.filter(is_sandbox=True).order_by("-id").first()
        guest.post(reverse("course:sandbox_microphone", kwargs={"cid": course.id}), {"on": "0"})
        course.refresh_from_db()
        self.assertFalse(course.classroom_microphone)


@override_settings(
    GUEST_SESSIONS_ENABLED=True,
    ALLOWED_HOSTS=["*", "testserver"],
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class RealAccountJoinsADemoTests(TestCase):
    """A real student typing a demo code joins as themselves."""

    def setUp(self):
        self.course = sandbox.create(User.objects.create_user("demo-host"))
        self.real = User.objects.create_user("real-student", first_name="Real", last_name="Student")
        self.client.force_login(self.real)

    def test_no_name_is_asked_and_no_new_account_is_made(self):
        page = self.client.get(reverse("course:sandbox_join"))
        self.assertNotContains(page, 'name="name"')
        self.assertContains(page, "Real Student")
        users = User.objects.count()
        response = self.client.post(reverse("course:sandbox_join"), {"code": self.course.join_code})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), users)
        member = self.course.members.get(user=self.real)
        self.assertEqual(member.role, "student")
        self.assertFalse(member.is_sandbox_guest)
        self.assertFalse(sandbox_guard.is_guest(self.real))

    def test_when_the_class_goes_only_the_membership_goes(self):
        self.client.post(reverse("course:sandbox_join"), {"code": self.course.join_code})
        sandbox.remove(self.course)
        self.assertTrue(User.objects.filter(pk=self.real.pk).exists())
        self.assertFalse(CourseMember.objects.filter(user=self.real).exists())
        # The session still names the deleted class; the next page must cope.
        response = self.client.get("/")
        self.assertLess(response.status_code, 500)

    def test_joining_twice_does_not_duplicate(self):
        for _ in range(2):
            self.client.post(reverse("course:sandbox_join"), {"code": self.course.join_code})
        self.assertEqual(self.course.members.filter(user=self.real).count(), 1)


@override_settings(GUEST_SESSIONS_ENABLED=True, ALLOWED_HOSTS=["*", "testserver"])
class RetiredTempSessionTests(TestCase):
    """The thing this replaced left buttons behind on Agora's course list."""

    def test_the_old_temporary_sessions_menu_is_gone(self):
        user = User.objects.create_superuser("root2", "root2@example.com", "x")
        client = Client()
        client.force_login(user)

        body = client.get(reverse("course:list")).content.decode()

        # It called /course/temp/..., which no longer routes anywhere.
        self.assertNotIn("Temporary Sessions", body)
        self.assertNotIn("/course/temp", body)
