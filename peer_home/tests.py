from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse

from peer_course.base import CourseBase
from peer_course.models import (
    Course,
    CourseMember,
    CourseParticipation,
    CourseSession,
    HandRaise,
)
from peer_home.management.commands.manage_live_sessions import (
    run_live_session_maintenance,
)
from peer_lecture.models import Lecture

from .views import HomeViews


IN_MEMORY_CHANNELS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


class TestBasicAuth(TestCase):
    def test_login_required(self):
        response = self.client.get(reverse("home:home"), follow=True)
        self.assertEqual(response.context["user"].is_authenticated, False)
        self.assertTrue("login" in response.redirect_chain[0][0])

    def test_login(self):
        password = "12345"
        user = User._default_manager.create_user(username="testuser", password=password)

        self.client.login(username=user.username, password=password)  # login
        response = self.client.get(reverse("home:home"), follow=True)

        self.assertEqual(response.context["user"].is_authenticated, True)
        self.assertEqual(response.context["user"].username, user.username)


class CourseTestMixin:
    def setUp(self):
        self.user = User.objects.create_user(username="instructor", password="test-password-123")
        self.course = CourseBase.create(
            self.user,
            "Test Agora Course",
            browsable=False,
        )
        CourseBase._enroll(self.user, self.course, "instructor")
        self.client.login(username=self.user.username, password="test-password-123")


class TestCourseSelect(CourseTestMixin, TestCase):

    def test_logged_in(self):
        response = self.client.get(reverse("home:home"), follow=True)
        self.assertEqual(response.context["user"].is_authenticated, True)

    def test_chosen_course_required(self):
        response = self.client.get(reverse("home:home"), follow=True)
        self.assertTrue(reverse("course:list") in response.redirect_chain[0][0])


class TestHomepage(CourseTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.get(
            reverse("course:view", kwargs={"cid": self.course.id})
        )

    def test_course_chosen(self):
        response = self.client.get(reverse("home:home"), follow=True)
        self.assertTrue(
            not response.redirect_chain  # either no redirection
            or reverse(  # or first redirection is not to course:list to choose a course
                "course:list"
            )
            not in response.redirect_chain[0][0]
        )


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class AgoraLiveSelectionTests(TestCase):
    """Regression coverage for live selection and cross-midnight sessions."""

    def setUp(self):
        self.instructor = User.objects.create_user("live-instructor", password="pw")
        self.student = User.objects.create_user(
            "live-student",
            password="pw",
            first_name="Ada",
            last_name="Lovelace",
        )
        self.course = Course.objects.create(
            displayname="Overnight Agora",
            browsable=False,
            archived=False,
            auto_start=False,
            course_timezone="America/Vancouver",
            allow_bonus=False,
        )
        self.instructor_member = CourseMember.objects.create(
            course=self.course,
            user=self.instructor,
            role="instructor",
            hand_up=True,
        )
        self.student_member = CourseMember.objects.create(
            course=self.course,
            user=self.student,
            role="student",
            hand_up=True,
        )
        self.session = CourseSession.objects.create(
            course=self.course,
            session_date=date(2026, 8, 26),
            start_time_pst=time(23, 0),
            end_time_pst=time(1, 0),
        )
        # 00:30 PDT on the day after session_date.
        self.now = datetime(2026, 8, 27, 7, 30, tzinfo=dt_timezone.utc)
        self.client.force_login(self.instructor)
        browser_session = self.client.session
        browser_session["course_id"] = self.course.pk
        browser_session.save()

    def post_at_fixed_time(self, url, data=None):
        with (
            patch("peer_home.views.timezone.now", return_value=self.now),
            patch("peer_course.sessions.timezone.now", return_value=self.now),
        ):
            return self.client.post(url, data or {})

    def get_at_fixed_time(self, url):
        with (
            patch("peer_home.views.timezone.now", return_value=self.now),
            patch("peer_course.sessions.timezone.now", return_value=self.now),
        ):
            return self.client.get(url)

    def test_random_selection_replay_is_idempotent_across_tabs(self):
        url = reverse("home:random_student")
        first = self.post_at_fixed_time(url, {"selection_version": 0})
        second = self.post_at_fixed_time(url, {"selection_version": 0})
        self.student_member.hand_up_list_2 = True
        self.student_member.save(update_fields=["hand_up_list_2"])
        other_tab = self.post_at_fixed_time(
            reverse("home:random_student_blue"),
            {"selection_version": 0},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["duplicate"])
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(first.json()["selection_version"], second.json()["selection_version"])
        self.assertEqual(first.json()["student_id"], second.json()["student_id"])
        self.assertTrue(other_tab.json()["duplicate"])
        self.assertEqual(other_tab.json()["list_id"], 1)
        self.assertEqual(
            CourseParticipation.objects.filter(
                session=self.session,
                spoke_upon_participation=True,
                real_participation=True,
                count_in_calculations=True,
            ).count(),
            1,
        )

    def test_choose_next_replay_is_idempotent_and_invalid_list_is_400(self):
        url = f"/choose_next/{self.student_member.pk}/1/"
        missing_version = self.post_at_fixed_time(url)
        first = self.post_at_fixed_time(url, {"selection_version": 0})
        second = self.post_at_fixed_time(url, {"selection_version": 0})
        invalid = self.post_at_fixed_time(
            f"/choose_next/{self.student_member.pk}/9/",
            {"selection_version": 0},
        )

        self.assertEqual(missing_version.status_code, 400)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(
            CourseParticipation.objects.filter(
                session=self.session,
                spoke_upon_participation=True,
                real_participation=True,
                count_in_calculations=True,
            ).count(),
            1,
        )
        self.assertEqual(invalid.status_code, 400)

    def test_a_student_who_has_spoken_keeps_earning_when_no_phantom_points_are_paid(self):
        # AI 100 counts every raise all term. The recorder used to stop
        # crediting a student once they had been chosen -- the right rule only
        # where phantom points replace it -- so a student whose hand was up
        # through eight later calls was credited for none of them.
        other = CourseMember.objects.create(
            course=self.course,
            user=User.objects.create_user("live-other", password="pw"),
            role="student",
            hand_up=True,
        )
        first = self.post_at_fixed_time(
            reverse("home:random_student"), {"selection_version": 0}
        )
        picked_username = first.json()["student_id"]
        picked, waiting = (
            (self.student_member, other)
            if picked_username == self.student.username
            else (other, self.student_member)
        )
        # The chosen student raises a hand again; the instructor calls again.
        picked.refresh_from_db()
        picked.hand_up = True
        picked.save(update_fields=["hand_up"])
        # Only the other student can be chosen now, so the picked one waits.
        waiting.refresh_from_db()
        self.assertTrue(waiting.hand_up)
        with patch("peer_home.views.np.random.choice", side_effect=lambda pool, p=None: waiting):
            second = self.post_at_fixed_time(
                reverse("home:random_student"),
                {"selection_version": first.json()["selection_version"]},
            )
        self.assertEqual(second.status_code, 200)

        credited = CourseParticipation.objects.filter(
            participant=picked, session=self.session,
            spoke_upon_participation=False, count_in_calculations=True,
        ).aggregate(points=Sum("participation_points_gained"))["points"]
        self.assertEqual(credited, 1)

    def test_a_student_who_has_spoken_is_paid_in_phantom_points_where_the_course_does_that(self):
        self.course.allow_bonus = True
        self.course.save(update_fields=["allow_bonus"])
        other = CourseMember.objects.create(
            course=self.course,
            user=User.objects.create_user("live-other-2", password="pw"),
            role="student",
            hand_up=True,
        )
        first = self.post_at_fixed_time(
            reverse("home:random_student"), {"selection_version": 0}
        )
        picked_username = first.json()["student_id"]
        picked, waiting = (
            (self.student_member, other)
            if picked_username == self.student.username
            else (other, self.student_member)
        )
        picked.refresh_from_db()
        picked.hand_up = True
        picked.save(update_fields=["hand_up"])
        with patch("peer_home.views.np.random.choice", side_effect=lambda pool, p=None: waiting):
            self.post_at_fixed_time(
                reverse("home:random_student"),
                {"selection_version": first.json()["selection_version"]},
            )

        rows = CourseParticipation.objects.filter(participant=picked, session=self.session)
        self.assertEqual(rows.filter(spoke_upon_participation=False).count(), 0)
        self.assertEqual(rows.get(spoke_upon_participation=True).bonus_points, 1)

    def test_a_queue_that_does_not_count_is_a_plain_uniform_draw(self):
        self.course.included_lists = ["1"]
        self.course.save(update_fields=["included_lists"])
        self.student_member.hand_up_list_2 = True
        self.student_member.save(update_fields=["hand_up_list_2"])
        calls = []

        def fake_choice(pool, p=None):
            calls.append(p)
            return pool[0]

        with patch("peer_home.views.np.random.choice", side_effect=fake_choice):
            response = self.post_at_fixed_time(
                reverse("home:random_student_blue"), {"selection_version": 0}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [None], "the excluded queue was weighted")

    def test_a_queue_that_counts_is_weighted(self):
        calls = []

        def fake_choice(pool, p=None):
            calls.append(p)
            return pool[0]

        with patch("peer_home.views.np.random.choice", side_effect=fake_choice):
            self.post_at_fixed_time(reverse("home:random_student"), {"selection_version": 0})

        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0])

    def test_the_instructor_is_shown_the_preferred_name(self):
        from peer_account.models import Profile

        Profile.objects.update_or_create(
            user=self.student,
            defaults={"preferred_first_name": "Addie", "preferred_last_name": ""},
        )

        response = self.post_at_fixed_time(
            reverse("home:random_student"), {"selection_version": 0}
        )

        self.assertEqual(response.json()["student"], "Addie Lovelace")
        # And in the raised-hands lists, which are read off the same screen.
        self.student_member.refresh_from_db()
        self.student_member.hand_up = True
        self.student_member.save(update_fields=["hand_up"])
        listing = self.get_at_fixed_time(reverse("home:count_hands_up"))
        names = [row[:2] for row in listing.json().get("list_of_spoken", [])]
        self.assertIn(["Addie", "Lovelace"], names)

    def test_updated_selection_version_allows_an_immediate_deliberate_next_pick(self):
        first = self.post_at_fixed_time(
            reverse("home:random_student"),
            {"selection_version": 0},
        )
        self.student_member.hand_up_list_2 = True
        self.student_member.save(update_fields=["hand_up_list_2"])

        second = self.post_at_fixed_time(
            reverse("home:random_student_blue"),
            {"selection_version": first.json()["selection_version"]},
        )

        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["duplicate"])
        self.assertEqual(second.json()["list_id"], 2)
        self.assertEqual(
            CourseParticipation.objects.filter(
                session=self.session,
                spoke_upon_participation=True,
                real_participation=True,
                count_in_calculations=True,
            ).count(),
            2,
        )

    def test_selected_student_exposes_only_protected_photo_route(self):
        profile = self.student.profile
        profile.photo.name = "profile_photos/private.jpg"
        profile.save(update_fields=["photo"])

        response = self.post_at_fixed_time(
            reverse("home:random_student"),
            {"selection_version": 0},
        )

        self.assertEqual(
            response.json()["student_photo"],
            reverse("account:protected_photo", kwargs={"uid": self.student.pk}),
        )
        self.assertNotIn("/media/", response.json()["student_photo"])

    def test_status_counts_and_undo_keep_using_overnight_session(self):
        participation = CourseParticipation.objects.create(
            participant=self.student_member,
            session=self.session,
            participation_list=1,
            spoke_upon_participation=True,
            real_participation=True,
            count_in_calculations=True,
        )

        status = self.get_at_fixed_time(reverse("home:check_status"))
        counts = self.get_at_fixed_time(reverse("home:count_hands_up"))
        undone = self.post_at_fixed_time(reverse("home:undo"))

        self.assertTrue(status.json()["session_active"])
        self.assertEqual(counts.json()["selection_versions"]["1"], participation.pk)
        self.assertEqual(counts.json()["count_total_spoken"][0], 1)
        self.assertEqual(undone.status_code, 200)
        participation.refresh_from_db()
        self.assertFalse(participation.count_in_calculations)

    def test_manual_session_with_separate_grading_date_remains_resolvable(self):
        self.course.manual_override = True
        self.course.save(update_fields=["manual_override"])
        self.session.session_date = date(2026, 8, 20)
        self.session.start_time_pst = time(10, 0)
        self.session.end_time_pst = time(11, 0)
        self.session.save(update_fields=["session_date", "start_time_pst", "end_time_pst"])
        Lecture.objects.create(course=self.course)

        response = self.post_at_fixed_time(
            reverse("home:random_student"),
            {"selection_version": 0},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["student_id"], self.student.username)
        self.assertTrue(CourseParticipation.objects.filter(session=self.session).exists())

    def test_manual_lecture_maintenance_uses_lecture_clock_not_grading_date(self):
        self.course.manual_override = True
        self.course.save(update_fields=["manual_override"])
        self.session.session_date = date(2026, 8, 20)
        self.session.save(update_fields=["session_date"])
        lecture = Lecture.objects.create(course=self.course)
        Lecture.objects.filter(pk=lecture.pk).update(
            start_time=self.now - self.course.default_session_duration / 2,
        )
        lecture.refresh_from_db()

        with patch("peer_home.views.timezone.now", return_value=self.now):
            result = HomeViews.manage_lecture(
                None,
                self.course,
                session_active=True,
                current_date=self.now.date(),
            )

        self.assertEqual(result.pk, lecture.pk)
        lecture.refresh_from_db()
        self.assertIsNone(lecture.end_time)
        self.session.refresh_from_db()
        self.assertFalse(self.session.ended)

    def test_maintenance_command_ends_expired_manual_session_consistently(self):
        self.course.manual_override = True
        self.course.manually_started = True
        self.course.default_session_duration = timedelta(hours=1)
        self.course.save(
            update_fields=[
                "manual_override",
                "manually_started",
                "default_session_duration",
            ]
        )
        self.session.session_date = date(2026, 8, 20)
        self.session.save(update_fields=["session_date"])
        lecture = Lecture.objects.create(course=self.course)
        Lecture.objects.filter(pk=lecture.pk).update(
            start_time=self.now - timedelta(hours=2),
        )

        with (
            patch(
                "peer_home.management.commands.manage_live_sessions.timezone.now",
                return_value=self.now,
            ),
            patch("peer_home.views.timezone.now", return_value=self.now),
            patch("peer_course.sessions.timezone.now", return_value=self.now),
        ):
            result = run_live_session_maintenance()

        self.assertTrue(result)
        lecture.refresh_from_db()
        self.assertIsNotNone(lecture.end_time)
        self.session.refresh_from_db()
        self.assertTrue(self.session.ended)
        self.course.refresh_from_db()
        self.assertFalse(self.course.manual_override)
        self.assertFalse(self.course.manually_started)

    def test_student_and_instructor_can_render_dashboard(self):
        instructor_response = self.get_at_fixed_time(reverse("home:agora_dash"))
        self.client.force_login(self.student)
        browser_session = self.client.session
        browser_session["course_id"] = self.course.pk
        browser_session.save()
        student_response = self.get_at_fixed_time(reverse("home:agora_dash"))

        self.assertEqual(instructor_response.status_code, 200)
        self.assertEqual(student_response.status_code, 200)
        self.assertTrue(student_response.context["is_student"])

    def test_dashboard_and_status_gets_never_apply_scheduled_transitions(self):
        self.session.delete()
        self.course.auto_start = True
        self.course.save(update_fields=["auto_start"])
        CourseSession.objects.create(
            course=self.course,
            is_template=True,
            session_date=date(2026, 8, 1),
            session_end_date=date(2026, 9, 1),
            active_days="thu",
            start_time_pst=time(0, 0),
            end_time_pst=time(1, 0),
        )
        canceled = CourseSession.objects.create(
            course=self.course,
            is_template=True,
            session_date=date(2026, 8, 1),
            session_end_date=date(2026, 9, 1),
            active_days="thu",
            start_time_pst=time(0, 0),
            end_time_pst=time(0, 15),
            cancel_next_session=True,
        )

        with (
            patch(
                "peer_home.views.HomeViews.manage_lecture",
                side_effect=AssertionError("GET attempted lecture maintenance"),
            ),
            patch(
                "peer_home.views.HomeViews._spawn_if_missing",
                side_effect=AssertionError("GET attempted session creation"),
            ),
        ):
            responses = [
                self.get_at_fixed_time(reverse("home:agora_dash")),
                self.get_at_fixed_time(reverse("home:agora_dash")),
                self.get_at_fixed_time(reverse("home:check_status")),
                self.get_at_fixed_time(reverse("home:check_status")),
            ]

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertFalse(
            CourseSession.objects.filter(course=self.course, is_template=False).exists()
        )
        self.assertFalse(Lecture.objects.filter(course=self.course).exists())
        canceled.refresh_from_db()
        self.assertTrue(canceled.cancel_next_session)

    def test_scheduler_remains_the_owner_of_scheduled_transitions(self):
        self.session.delete()
        self.course.auto_start = True
        self.course.save(update_fields=["auto_start"])
        CourseSession.objects.create(
            course=self.course,
            is_template=True,
            session_date=date(2026, 8, 1),
            session_end_date=date(2026, 9, 1),
            active_days="thu",
            start_time_pst=time(0, 0),
            end_time_pst=time(1, 0),
        )

        with (
            patch(
                "peer_home.management.commands.manage_live_sessions.timezone.now",
                return_value=self.now,
            ),
            patch("peer_home.views.timezone.now", return_value=self.now),
            patch("peer_course.sessions.timezone.now", return_value=self.now),
        ):
            result = run_live_session_maintenance()

        self.assertTrue(result)
        self.assertTrue(
            CourseSession.objects.filter(course=self.course, is_template=False).exists()
        )
        self.assertTrue(
            Lecture.objects.filter(course=self.course, end_time__isnull=True).exists()
        )


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class ScheduleEnteredAheadLifecycleTests(TestCase):
    """Closing September must not close the last lecture in December."""

    def setUp(self):
        self.course = Course.objects.create(
            displayname="Seventeen scheduled lectures",
            browsable=False,
            archived=False,
            auto_start=True,
            course_timezone="America/Vancouver",
        )
        self.current = CourseSession.objects.create(
            course=self.course,
            session_date=date(2026, 9, 9),
            start_time_pst=time(14, 0),
            end_time_pst=time(15, 30),
        )
        self.final = CourseSession.objects.create(
            course=self.course,
            session_date=date(2026, 12, 2),
            start_time_pst=time(14, 0),
            end_time_pst=time(15, 30),
        )
        self.student = CourseMember.objects.create(
            course=self.course,
            user=User.objects.create_user("scheduled-ahead-raised"),
            role="student",
        )
        self.started = datetime(2026, 9, 9, 21, 0, tzinfo=dt_timezone.utc)
        self.after = datetime(2026, 9, 9, 22, 31, tzinfo=dt_timezone.utc)
        with patch("peer_course.sessions.timezone.now", return_value=self.started):
            self.student.hand_up = True
            self.student.save(update_fields=["hand_up"])
        self.lecture = Lecture.objects.create(course=self.course)
        Lecture.objects.filter(pk=self.lecture.pk).update(start_time=self.started)

    def test_closing_lecture_ends_the_session_that_ran_and_clears_hands(self):
        with (
            patch("peer_home.views.timezone.now", return_value=self.after),
            patch("peer_course.sessions.timezone.now", return_value=self.after),
        ):
            result = HomeViews.manage_lecture(
                request=None,
                course=self.course,
                session_active=False,
                current_date=self.current.session_date,
            )

        self.assertIsNone(result)
        self.current.refresh_from_db()
        self.final.refresh_from_db()
        self.lecture.refresh_from_db()
        self.student.refresh_from_db()
        self.assertTrue(self.current.ended)
        self.assertEqual(self.current.actual_end_time, time(15, 30))
        self.assertFalse(self.final.ended)
        self.assertEqual(
            self.lecture.end_time,
            datetime(2026, 9, 9, 22, 30, tzinfo=dt_timezone.utc),
        )
        self.assertFalse(self.student.hand_up)
        raise_row = HandRaise.objects.get(member=self.student)
        self.assertIsNotNone(raise_row.resolved_at)
        self.assertEqual(raise_row.resolution, HandRaise.Resolution.CLEARED)

    def test_maintenance_repairs_a_future_session_marked_ended(self):
        self.final.ended = True
        self.final.actual_end_time = time(15, 30)
        self.final.save(update_fields=["ended", "actual_end_time"])

        HomeViews.maintain_scheduled_sessions(
            self.course,
            self.current.session_date,
            time(12, 0),
            "wed",
        )

        self.final.refresh_from_db()
        self.assertFalse(self.final.ended)
        self.assertIsNone(self.final.actual_end_time)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class StudentStatusPollTests(TestCase):
    """/check_status/ is fetched by every student page during a lecture.

    Since 2026-09-15 the page relies on WebSocket broadcasts and only calls
    this view to reconcile, but each call still has to be cheap: at 400
    students a handful of extra queries per call is the difference between a
    responsive lecture and a saturated server.
    """

    def setUp(self):
        self.instructor = User.objects.create_user("status-instructor", password="pw")
        self.student = User.objects.create_user("status-student", password="pw")
        self.course = Course.objects.create(
            displayname="Status Course",
            browsable=False,
            archived=False,
            auto_start=False,
            course_timezone="America/Vancouver",
            allow_bonus=False,
            # A course that may call a student twice tells them so once
            # they have spoken; that note is part of the status payload.
            spoken_weight=0.5,
        )
        self.instructor_member = CourseMember.objects.create(
            course=self.course,
            user=self.instructor,
            role="instructor",
            hand_up=True,
            hand_up_list_3=True,
        )
        self.student_member = CourseMember.objects.create(
            course=self.course,
            user=self.student,
            role="student",
            hand_up=True,
        )
        self.session = CourseSession.objects.create(
            course=self.course,
            session_date=date(2026, 9, 15),
            start_time_pst=time(14, 0),
            end_time_pst=time(15, 30),
        )
        # 14:30 PDT on the session date.
        self.now = datetime(2026, 9, 15, 21, 30, tzinfo=dt_timezone.utc)
        self.client.force_login(self.student)
        browser_session = self.client.session
        browser_session["course_id"] = self.course.pk
        browser_session.save()

    def status_at(self, when):
        with (
            patch("peer_home.views.timezone.now", return_value=when),
            patch("peer_course.sessions.timezone.now", return_value=when),
        ):
            return self.client.get(reverse("home:check_status"))

    def test_student_sees_which_lists_are_open_and_own_hand(self):
        data = self.status_at(self.now).json()
        self.assertTrue(data["session_active"])
        # Green: instructor open, student's hand is up.
        self.assertFalse(data["disbale_green"])
        self.assertIn("hand is up", data["status_green"])
        # Red: instructor open, student's hand is down.
        self.assertFalse(data["disbale_red"])
        self.assertIn("hand is down", data["status_red"])
        # Blue and yellow: no instructor has them open.
        self.assertTrue(data["disbale_blue"])
        self.assertTrue(data["disbale_yellow"])
        self.assertNotIn("status_blue", data)
        self.assertFalse(data["poll_active"])
        self.assertEqual(data["total_points_today"], 0)
        self.assertEqual(data["spoken_note"], "")

    def test_outside_the_session_window_reports_inactive(self):
        data = self.status_at(datetime(2026, 9, 15, 23, 0, tzinfo=dt_timezone.utc)).json()
        self.assertFalse(data["session_active"])
        self.assertEqual(data["total_points_today"], 0)

    def test_spoken_note_comes_from_the_same_participation_rows(self):
        CourseParticipation.objects.create(
            participant=self.student_member,
            session=self.session,
            participation_list=1,
            spoke_upon_participation=True,
            real_participation=True,
            count_in_calculations=True,
        )
        data = self.status_at(self.now).json()
        self.assertTrue(data["session_active"])
        self.assertIn("already spoken", data["spoken_note"])

    def test_status_stays_within_its_query_budget(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.status_at(self.now)  # warm anything cached per process
        with CaptureQueriesContext(connection) as queries:
            response = self.status_at(self.now)
        self.assertEqual(response.status_code, 200)
        # Session, user, course, two membership checks, one query for all
        # four lists, lecture, session, participation rows, scheme, and the
        # hand-raise count behind lecture_status: eleven. Before the lists
        # were folded into one query and the session was resolved once, the
        # same request cost sixteen.
        self.assertLessEqual(len(queries), 11, [q["sql"][:90] for q in queries])
