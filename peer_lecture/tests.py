import json
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from peer_course.models import Course, CourseMember, CourseParticipation, CourseSession

from .models import Lecture, Message, Poll, PollResult, sanitize_poll_html
from .routing import websocket_urlpatterns


IN_MEMORY_CHANNELS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


def make_course(name):
    return Course.objects.create(
        displayname=name,
        browsable=True,
        archived=False,
    )


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class LiveHttpSecurityTests(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user("instructor", password="pw")
        self.ta = User.objects.create_user("ta", password="pw")
        self.student = User.objects.create_user(
            "student", password="pw", first_name="Student", last_name="One"
        )
        self.other_student = User.objects.create_user(
            "other", password="pw", first_name="Student", last_name="Two"
        )
        self.outsider = User.objects.create_user("outsider", password="pw")
        self.course = make_course("Course One")
        self.other_course = make_course("Course Two")
        for user, role in (
            (self.instructor, "instructor"),
            (self.ta, "ta"),
            (self.student, "student"),
            (self.other_student, "student"),
        ):
            CourseMember.objects.create(course=self.course, user=user, role=role)
        CourseMember.objects.create(
            course=self.other_course,
            user=self.outsider,
            role="instructor",
        )
        self.lecture = Lecture.objects.create(course=self.course)
        self.other_lecture = Lecture.objects.create(course=self.other_course)
        self.template = Poll.objects.create(
            course=self.course,
            title="Visible poll",
            answer=["A"],
            answer_options=json.dumps(["A", "B"]),
            dont_save_answer=False,
            poll_type="iclicker",
            is_visible_to_students=True,
        )
        self.running_poll = Poll.objects.create(
            course=self.course,
            lecture=self.lecture,
            duplicate_of=self.template,
            title=self.template.title,
            answer=self.template.answer,
            answer_options=self.template.answer_options,
            dont_save_answer=False,
            poll_type="iclicker",
            start_time=timezone.now(),
        )
        self.other_poll = Poll.objects.create(
            course=self.other_course,
            lecture=self.other_lecture,
            title="Other course",
            answer=["X"],
            answer_options=json.dumps(["X", "Y"]),
            dont_save_answer=False,
            poll_type="iclicker",
            start_time=timezone.now(),
        )

    def select_course(self, user, course=None):
        self.client.force_login(user)
        session = self.client.session
        session["course_id"] = (course or self.course).pk
        session.save()

    def test_poll_results_require_login_membership_and_course_scope(self):
        url = reverse("home:get_poll_results_json", args=[self.running_poll.pk])
        self.assertEqual(self.client.get(url).status_code, 302)

        self.select_course(self.outsider, self.course)
        self.assertEqual(self.client.get(url).status_code, 403)

        self.select_course(self.instructor)
        other_url = reverse("home:get_poll_results_json", args=[self.other_poll.pk])
        self.assertEqual(self.client.get(other_url).status_code, 404)

    def test_text_results_never_disclose_other_students_to_a_student(self):
        self.running_poll.poll_type = "text"
        self.running_poll.answer_options = json.dumps([])
        self.running_poll.end_time = timezone.now()
        self.running_poll.save()
        PollResult.objects.create(
            poll=self.running_poll,
            auth_user=self.student,
            answer=["my response"],
        )
        PollResult.objects.create(
            poll=self.running_poll,
            auth_user=self.other_student,
            answer=["private response"],
        )

        self.select_course(self.student)
        response = self.client.get(
            reverse("home:get_poll_results_json", args=[self.running_poll.pk])
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["answer"] for row in payload["responses"]], ["my response"])

        self.template.is_visible_to_students = False
        self.template.save(update_fields=["is_visible_to_students"])
        self.assertEqual(
            self.client.get(
                reverse("home:get_poll_results_json", args=[self.running_poll.pk])
            ).status_code,
            403,
        )

    def test_poll_backup_is_instructor_only(self):
        url = reverse("home:download_all_polls_json")
        self.select_course(self.student)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.select_course(self.instructor)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["polls"][0]["title"], self.template.title)

    def test_student_can_only_answer_current_selected_course_poll(self):
        self.select_course(self.student)
        url = reverse("home:polling_student")
        self.assertEqual(
            self.client.get(
                reverse("home:get_poll_results_json", args=[self.running_poll.pk])
            ).status_code,
            403,
        )
        response = self.client.post(
            url,
            {"poll_id": self.running_poll.pk, "poll-answer": "not-an-option"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            url,
            {"poll_id": self.running_poll.pk, "poll-answer": "A"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            PollResult.objects.get(poll=self.running_poll, auth_user=self.student).answer,
            ["A"],
        )
        self.client.post(
            url,
            {"poll_id": self.running_poll.pk, "poll-answer": "B"},
        )
        self.assertEqual(
            PollResult.objects.filter(poll=self.running_poll, auth_user=self.student).count(),
            1,
        )
        self.assertEqual(
            PollResult.objects.get(poll=self.running_poll, auth_user=self.student).answer,
            ["B"],
        )

        response = self.client.post(
            url,
            {"poll_id": self.other_poll.pk, "poll-answer": "X"},
        )
        self.assertEqual(response.status_code, 404)

    def test_message_board_rejects_disabled_and_cross_course_operations(self):
        self.lecture.enable_messages = False
        self.lecture.save(update_fields=["enable_messages"])
        self.select_course(self.student)
        response = self.client.post(
            reverse("home:message_student"),
            {"message": "hello"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Message.objects.exists())

        other_message = Message.objects.create(
            lecture=self.other_lecture,
            auth_user=self.outsider,
            message="other course",
        )
        self.select_course(self.instructor)
        response = self.client.post(
            reverse("home:message_instructor"),
            {"broadcast-message": other_message.pk},
        )
        self.assertEqual(response.status_code, 404)
        other_message.refresh_from_db()
        self.assertFalse(other_message.broadcast)

    def test_blocked_list_exposes_only_display_fields(self):
        self.lecture.blocked_students.add(self.student)
        self.select_course(self.instructor)
        response = self.client.get(
            reverse("home:message_instructor"),
            {"get-blocked-list": "true"},
        )
        self.assertEqual(response.status_code, 200)
        row = response.json()["blocked_students"][0]
        self.assertEqual(set(row), {"id", "first_name", "last_name"})

    def test_incremental_message_delivery_uses_exact_scoped_primary_key(self):
        first = Message.objects.create(
            lecture=self.lecture,
            auth_user=self.student,
            message="deliver this exact row",
        )
        Message.objects.create(
            lecture=self.lecture,
            auth_user=self.other_student,
            message="newer row must not be substituted",
        )
        other_message = Message.objects.create(
            lecture=self.other_lecture,
            auth_user=self.outsider,
            message="other course",
        )
        self.select_course(self.instructor)
        url = reverse("home:message_instructor")

        response = self.client.get(
            url,
            {"get-messages": "true", "message-id": first.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message_id"], first.pk)
        self.assertIn("deliver this exact row", response.json()["html_content"])
        self.assertNotIn("newer row must not be substituted", response.json()["html_content"])
        self.assertEqual(
            self.client.get(
                url,
                {"get-messages": "true", "message-id": other_message.pk},
            ).status_code,
            404,
        )

    def test_delete_archives_template_and_preserves_history(self):
        self.running_poll.end_time = timezone.now()
        self.running_poll.save(update_fields=["end_time"])
        self.select_course(self.instructor)
        response = self.client.post(
            reverse("home:polling_instructor"),
            {"delete-poll": "true", "saved-poll-id": self.template.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.template.refresh_from_db()
        self.assertTrue(self.template.archived)
        self.assertTrue(Poll.objects.filter(pk=self.running_poll.pk).exists())

    def test_move_hands_is_list_only_and_idempotent(self):
        self.running_poll.end_time = timezone.now()
        self.running_poll.save(update_fields=["end_time"])
        self.select_course(self.instructor)
        url = reverse("home:polling_instructor")
        response = self.client.post(url, {"move-hands": self.running_poll.pk})
        self.assertEqual(response.status_code, 400)

        list_template = Poll.objects.create(
            course=self.course,
            title="Lists",
            answer=[],
            answer_options=json.dumps(["Green", "Blue", "Red", "Yellow"]),
            dont_save_answer=False,
            poll_type="list",
        )
        list_poll = Poll.objects.create(
            course=self.course,
            lecture=self.lecture,
            duplicate_of=list_template,
            title="Lists",
            answer=[],
            answer_options=list_template.answer_options,
            dont_save_answer=False,
            poll_type="list",
            start_time=timezone.now() - timedelta(minutes=2),
            end_time=timezone.now() - timedelta(minutes=1),
        )
        PollResult.objects.create(
            poll=list_poll,
            auth_user=self.student,
            answer=["Blue"],
        )
        local_now = timezone.localtime(timezone.now(), timezone.get_current_timezone())
        CourseSession.objects.create(
            course=self.course,
            # The window is dated from its start, not from now. Taking the
            # date from `now` while taking the start time from an hour earlier
            # wraps past midnight between 00:00 and 01:00 -- the session then
            # claimed to start at 23:0x tonight, was not active, and these
            # tests failed for the first hour of every day.
            session_date=(local_now - timedelta(hours=1)).date(),
            start_time_pst=(local_now - timedelta(hours=1)).time(),
            end_time_pst=(local_now + timedelta(hours=1)).time(),
        )

        first = self.client.post(url, {"move-hands": list_poll.pk})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["moved_count"], 1)
        second = self.client.post(url, {"move-hands": list_poll.pk})
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["already_moved"])
        self.assertEqual(CourseParticipation.objects.filter(session__course=self.course).count(), 1)
        membership = CourseMember.objects.get(course=self.course, user=self.student)
        self.assertTrue(membership.hand_up_list_2)
        self.assertFalse(membership.hand_up)

    def test_lecture_lifecycle_requires_post(self):
        self.lecture.end_time = timezone.now()
        self.lecture.save(update_fields=["end_time"])
        self.select_course(self.instructor)
        url = reverse("home:polling_instructor")
        get_response = self.client.get(url, {"toggle-lecture": "start"})
        self.assertEqual(get_response.status_code, 200)
        self.assertFalse(Lecture.objects.filter(course=self.course, end_time__isnull=True).exists())
        post_response = self.client.post(url, {"toggle-lecture": "start"})
        self.assertEqual(post_response.status_code, 200)
        self.assertTrue(post_response.json()["begin"])
        self.assertEqual(
            Lecture.objects.filter(course=self.course, end_time__isnull=True).count(),
            1,
        )


class PollSanitizerTests(TestCase):
    def test_poll_html_removes_executable_content_and_keeps_formatting(self):
        dirty = (
            '<p onclick="alert(1)"><strong>Question</strong>'
            '<script>alert(2)</script>'
            '<a href="javascript:alert(3)" target="_blank">link</a>'
            '<img src="data:image/svg+xml;base64,PHN2Zz4=" onerror="alert(4)"></p>'
        )
        cleaned = sanitize_poll_html(dirty)
        self.assertIn("<strong>Question</strong>", cleaned)
        self.assertNotIn("script", cleaned.lower())
        self.assertNotIn("onclick", cleaned.lower())
        self.assertNotIn("javascript:", cleaned.lower())
        self.assertNotIn("svg+xml", cleaned.lower())

    def test_poll_html_with_katex_formula_does_not_crash(self):
        # Quill stores formulas as a ql-formula span wrapping KaTeX output,
        # which includes a <math> block. <math> is dropped with its content,
        # so its children must be skipped rather than unwrapped afterwards.
        formula = (
            '<p>Pick <span class="ql-formula" data-value="k&gt;1">'
            '<span contenteditable="false"><span class="katex">'
            '<span class="katex-mathml"><math xmlns="http://www.w3.org/1998/Math/MathML">'
            '<semantics><mrow><mi>k</mi><mo>&gt;</mo><mn>1</mn></mrow>'
            '<annotation encoding="application/x-tex">k&gt;1</annotation>'
            '</semantics></math></span>'
            '<span class="katex-html" aria-hidden="true"><span class="mord">k</span></span>'
            '</span></span></span></p>'
        )
        cleaned = sanitize_poll_html(formula)
        self.assertIn('class="ql-formula"', cleaned)
        self.assertIn('data-value="k&gt;1"', cleaned)
        self.assertNotIn("<math", cleaned)
        self.assertNotIn("aria-hidden", cleaned)

    def test_lecture_create_returns_the_existing_open_lecture(self):
        course = make_course("Lifecycle Course")
        first = Lecture.create(course.pk)
        second = Lecture.create(course.pk)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            Lecture.objects.filter(course=course, end_time__isnull=True).count(),
            1,
        )

    def test_database_rejects_a_second_open_lecture_for_the_course(self):
        course = make_course("Unique Lifecycle Course")
        Lecture.objects.create(course=course)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Lecture.objects.create(course=course)

        self.assertEqual(
            Lecture.objects.filter(course=course, end_time__isnull=True).count(),
            1,
        )


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class LiveWebsocketSecurityTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.member = User.objects.create_user("member", password="pw")
        self.second_member = User.objects.create_user("second-member", password="pw")
        self.outsider = User.objects.create_user("outsider", password="pw")
        self.course = make_course("Socket Course")
        CourseMember.objects.create(
            course=self.course,
            user=self.member,
            role="student",
        )
        CourseMember.objects.create(
            course=self.course,
            user=self.second_member,
            role="student",
        )

    def application_for(self, user):
        router = URLRouter(websocket_urlpatterns)

        async def application(scope, receive, send):
            scope["user"] = user
            await router(scope, receive, send)

        return application

    def test_socket_requires_same_origin_authenticated_course_membership(self):
        async def scenario():
            headers = [(b"host", b"testserver"), (b"origin", b"http://testserver")]
            member_socket = WebsocketCommunicator(
                self.application_for(self.member),
                f"/ws/{self.course.pk}/?auth_id={self.outsider.pk}",
                headers=headers,
            )
            connected, _detail = await member_socket.connect()
            self.assertTrue(connected)
            await member_socket.disconnect()

            outsider_socket = WebsocketCommunicator(
                self.application_for(self.outsider),
                f"/ws/{self.course.pk}/",
                headers=headers,
            )
            connected, _detail = await outsider_socket.connect()
            self.assertFalse(connected)

            cross_origin_socket = WebsocketCommunicator(
                self.application_for(self.member),
                f"/ws/{self.course.pk}/",
                headers=[(b"host", b"testserver"), (b"origin", b"https://evil.example")],
            )
            connected, _detail = await cross_origin_socket.connect()
            self.assertFalse(connected)

        async_to_sync(scenario)()

    def test_sender_user_id_suppresses_only_the_originating_socket(self):
        async def scenario():
            headers = [(b"host", b"testserver"), (b"origin", b"http://testserver")]
            sender = WebsocketCommunicator(
                self.application_for(self.member),
                f"/ws/{self.course.pk}/",
                headers=headers,
            )
            recipient = WebsocketCommunicator(
                self.application_for(self.second_member),
                f"/ws/{self.course.pk}/",
                headers=headers,
            )
            self.assertTrue((await sender.connect())[0])
            self.assertTrue((await recipient.connect())[0])

            await get_channel_layer().group_send(
                f"course_{self.course.pk}",
                {
                    "type": "send_message",
                    "key": "count-hands-up",
                    "send_auth_id": self.member.pk,
                },
            )
            self.assertTrue(await sender.receive_nothing(timeout=0.1))
            self.assertEqual(
                await recipient.receive_json_from(timeout=1),
                {"key": "count-hands-up", "value": None},
            )
            await sender.disconnect()
            await recipient.disconnect()

        async_to_sync(scenario)()

    @override_settings(WEBSOCKET_MEMBERSHIP_REVALIDATION_SECONDS=0)
    def test_deactivated_member_is_revoked_before_next_event(self):
        async def scenario():
            headers = [(b"host", b"testserver"), (b"origin", b"http://testserver")]
            socket = WebsocketCommunicator(
                self.application_for(self.member),
                f"/ws/{self.course.pk}/",
                headers=headers,
            )
            self.assertTrue((await socket.connect())[0])
            await database_sync_to_async(CourseMember.objects.filter(
                course=self.course,
                user=self.member,
            ).update)(active=False)
            await get_channel_layer().group_send(
                f"course_{self.course.pk}",
                {
                    "type": "send_message",
                    "key": "count-hands-up",
                    "send_auth_id": self.second_member.pk,
                },
            )
            output = await socket.receive_output(timeout=1)
            self.assertEqual(output["type"], "websocket.close")
            self.assertEqual(output["code"], 4403)

        async_to_sync(scenario)()
