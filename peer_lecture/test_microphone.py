import struct
import time
import uuid
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from peer_course.models import Course, CourseMember
from .microphone import HEADER, disable_classroom_microphone, select_microphone_student
from .models import ClassroomMicrophone, Lecture
from .routing import websocket_urlpatterns
from .tests import IN_MEMORY_CHANNELS


class MicrophoneFixture:
    def setUp(self):
        super().setUp()
        self.course = Course.objects.create(displayname="Microphone class", browsable=True, archived=False, classroom_microphone=True)
        self.instructor = User.objects.create_user("mic-instructor", password="pw")
        self.student = User.objects.create_user("mic-student", password="pw", first_name="Guolin")
        self.other = User.objects.create_user("mic-other", password="pw")
        self.ta = User.objects.create_user("mic-ta", password="pw")
        for user, role in [(self.instructor, "instructor"), (self.student, "student"), (self.other, "student"), (self.ta, "ta")]:
            member = CourseMember.objects.create(course=self.course, user=user, role=role)
            if user == self.student:
                self.member = member
        self.lecture = Lecture.objects.create(course=self.course)
        select_microphone_student(self.course.pk, self.member, self.lecture)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class MicrophonePagesTests(MicrophoneFixture, TestCase):
    def test_sound_only_requires_course_instructor(self):
        url = reverse("home:sound_only", args=[self.course.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        for user in [self.student, self.ta, self.other]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.instructor)
        response = self.client.get(url)
        self.assertContains(response, 'data-instructor="true"')
        self.assertContains(response, "Start classroom sound")
        self.assertNotContains(response, 'id="next_student"')

    def lecture_page(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["course_id"] = self.course.pk
        session.save()
        return self.client.get(reverse("home:agora_dash"))

    def test_panel_only_appears_where_the_course_turned_it_on(self):
        for user in [self.instructor, self.student]:
            self.assertContains(self.lecture_page(user), "data-classroom-microphone")
        Course.objects.filter(pk=self.course.pk).update(classroom_microphone=False)
        for user in [self.instructor, self.student]:
            response = self.lecture_page(user)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "data-classroom-microphone")
            self.assertNotContains(response, "microphone.js")

    def test_sound_only_is_not_found_while_the_course_has_it_off(self):
        Course.objects.filter(pk=self.course.pk).update(classroom_microphone=False)
        self.client.force_login(self.instructor)
        self.assertEqual(self.client.get(reverse("home:sound_only", args=[self.course.pk])).status_code, 404)

    def test_selection_leaves_no_microphone_state_while_off(self):
        other_course = Course.objects.create(displayname="No microphone", browsable=True, archived=False)
        member = CourseMember.objects.create(course=other_course, user=self.other, role="student")
        select_microphone_student(other_course.pk, member, Lecture.objects.create(course=other_course))
        self.assertFalse(ClassroomMicrophone.objects.filter(pk=other_course.pk).exists())

    def test_credit_is_on_public_about_page(self):
        response = self.client.get(reverse("account:about"))
        self.assertContains(response, "Guolin Wang")
        self.assertContains(response, "https://github.com/Guolinn/classroom-mic")

    def test_end_lecture_revokes_microphone(self):
        room = ClassroomMicrophone.objects.get(pk=self.course.pk)
        old_generation = room.generation
        room.sender_channel = "old.sender"
        room.last_speaker = self.member
        room.save()
        self.lecture.end_time = timezone.now()
        self.lecture.save(update_fields=["end_time"])
        room.refresh_from_db()
        self.assertIsNone(room.selected_member_id)
        self.assertIsNone(room.last_speaker_id)
        self.assertEqual(room.sender_channel, "")
        self.assertNotEqual(old_generation, room.generation)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class MicrophoneSocketsTests(MicrophoneFixture, TransactionTestCase):
    async def connect_as(self, user, origin=b"http://localhost", course=None):
        socket = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            f"/ws/{(course or self.course).pk}/microphone/",
            headers=[(b"host", b"localhost"), (b"origin", origin)],
        )
        socket.scope["user"] = user
        connected, code = await socket.connect()
        return socket, connected, code

    async def assert_refused(self, user, origin=b"http://localhost"):
        # Accepted, then closed with 4403: a close before accept reaches the
        # browser as 1006, which it would keep retrying.
        socket, connected, _ = await self.connect_as(user, origin)
        self.assertTrue(connected)
        message = await socket.receive_output()
        self.assertEqual(message, {"type": "websocket.close", "code": 4403})
        await socket.disconnect()

    async def until(self, socket, predicate):
        for _ in range(20):
            message = await socket.receive_json_from(timeout=2)
            if predicate(message):
                return message
        self.fail("Expected microphone state was not delivered")

    async def command(self, socket, command, **kwargs):
        await socket.send_json_to({"type": command, **kwargs})
        return await self.until(socket, lambda state: state.get("reply_to") == command)

    async def drain(self, socket):
        while not await socket.receive_nothing(timeout=0.02):
            await socket.receive_output(timeout=1)

    def packet(self, generation, sequence=0, age=0):
        return HEADER.pack(uuid.UUID(generation).bytes, sequence, time.time() * 1000 - age, 24000) + struct.pack("<480h", *([1000] * 480))

    async def pair(self):
        receiver, connected, _ = await self.connect_as(self.instructor)
        self.assertTrue(connected)
        await receiver.receive_json_from()
        sender, connected, _ = await self.connect_as(self.student)
        self.assertTrue(connected)
        await sender.receive_json_from()
        output = await self.command(receiver, "claim_output")
        await self.until(sender, lambda state: state.get("output_connected"))
        stream = await self.command(sender, "start", generation=output["generation"])
        self.assertTrue(stream["this_sender"])
        await self.drain(receiver)
        await self.drain(sender)
        return receiver, sender, stream

    def test_diagnostics_are_bounded_private_and_current_pair_only(self):
        import asyncio
        import json
        from unittest.mock import patch
        async def run():
            receiver, sender, stream = await self.pair()
            observer, _, _ = await self.connect_as(self.other)
            await observer.receive_json_from()
            report = {"event": "periodic", "bufferMs": 40, "frameLagMs": float("nan"),
                      "browser": "chromium", "version": "20261009c", "received": 10**50,
                      "name": "PRIVATE", "deviceId": "PRIVATE", "audio": "PRIVATE"}
            with patch("peer_lecture.microphone.AUDIO_LOG.info") as log:
                await observer.send_json_to({"type": "diagnostics", "generation": stream["generation"], "report": report})
                await asyncio.sleep(.05)
                self.assertFalse(log.called)
                await sender.send_json_to({"type": "diagnostics", "generation": stream["generation"], "report": report})
                await asyncio.sleep(.05)
                self.assertEqual(log.call_count, 1)
                payload = json.loads(log.call_args.args[1])
                self.assertEqual(payload["role"], "sender")
                self.assertEqual(payload["client"], {"event": "periodic", "bufferMs": 40, "browser": "chromium", "version": "20261009c"})
                self.assertNotIn("PRIVATE", log.call_args.args[1])
                # Repeated client reports must not flood logs.
                await sender.send_json_to({"type": "diagnostics", "generation": stream["generation"], "report": report})
                await receiver.send_json_to({"type": "diagnostics", "generation": str(uuid.uuid4()), "report": report})
                await asyncio.sleep(.05)
                self.assertEqual(log.call_count, 1)
            await sender.disconnect(); await receiver.disconnect(); await observer.disconnect()
        async_to_sync(run)()

    def test_diagnostics_do_not_accept_revoked_members(self):
        import asyncio
        from unittest.mock import patch
        async def run():
            receiver, sender, stream = await self.pair()
            await database_sync_to_async(CourseMember.objects.filter(pk=self.member.pk).update)(active=False)
            with patch("peer_lecture.microphone.AUDIO_LOG.info") as log:
                await sender.send_json_to({"type": "diagnostics", "generation": stream["generation"], "report": {"bufferMs": 40}})
                await asyncio.sleep(.05)
                self.assertFalse(log.called)
            await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_origin_login_and_course_membership(self):
        async def run():
            outsider = await database_sync_to_async(User.objects.create_user)("outsider")
            for user, origin in [(AnonymousUser(), b"http://localhost"), (outsider, b"http://localhost"), (self.student, b"https://evil.example")]:
                await self.assert_refused(user, origin)
        async_to_sync(run)()

    def test_only_instructors_claim_output_and_only_chosen_student_starts(self):
        async def run():
            teacher, connected, _ = await self.connect_as(self.instructor)
            await teacher.receive_json_from()
            output = await self.command(teacher, "claim_output")
            for user in [self.other, self.ta]:
                socket, connected, _ = await self.connect_as(user)
                state = await socket.receive_json_from()
                rejected = await self.command(socket, "claim_output")
                self.assertFalse(rejected["this_output"])
                rejected = await self.command(socket, "start", generation=state["generation"])
                self.assertFalse(rejected["this_sender"])
                rejected = await self.command(socket, "mute")
                self.assertEqual(rejected["selected_user_id"], self.student.pk)
                await socket.disconnect()
            await teacher.disconnect()
        async_to_sync(run)()

    def test_signaling_and_relay_are_only_routed_to_the_current_pair(self):
        async def run():
            receiver, sender, stream = await self.pair()
            observer, connected, _ = await self.connect_as(self.other)
            await observer.receive_json_from()
            signal = {"description": {"type": "offer", "sdp": "test"}}
            await sender.send_json_to({"type": "signal", "generation": stream["generation"], "signal": signal})
            delivered = await receiver.receive_json_from()
            self.assertEqual(delivered["signal"], signal)
            self.assertTrue(await observer.receive_nothing(timeout=0.05))
            await observer.send_json_to({"type": "signal", "generation": stream["generation"], "signal": signal})
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            # PCM cannot play alongside a direct connection.
            await sender.send_to(bytes_data=self.packet(stream["generation"]))
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            await self.command(receiver, "relay", generation=stream["generation"])
            await self.until(sender, lambda state: state.get("transport") == "relay")
            await self.drain(receiver); await self.drain(sender); await self.drain(observer)
            packet = self.packet(stream["generation"], sequence=1)
            await sender.send_to(bytes_data=packet)
            self.assertEqual(await receiver.receive_from(), packet)
            self.assertTrue(await observer.receive_nothing(timeout=0.05))
            await sender.disconnect(); await receiver.disconnect(); await observer.disconnect()
        async_to_sync(run)()

    def test_takeover_keeps_both_instructor_sessions_and_stops_the_old_stream(self):
        async def run():
            ipad, sender, stream = await self.pair()
            laptop, connected, _ = await self.connect_as(self.instructor)
            self.assertTrue(connected)
            await laptop.receive_json_from()
            new = await self.command(laptop, "claim_output")
            self.assertTrue(new["this_output"])
            self.assertNotEqual(stream["generation"], new["generation"])
            old = await self.until(ipad, lambda state: not state.get("this_output"))
            self.assertFalse(old["streaming"])
            old_sender = await self.until(sender, lambda state: not state.get("this_sender"))
            self.assertFalse(old_sender["streaming"])
            # The iPad stays connected as a controller and can mute the student.
            muted = await self.command(ipad, "mute")
            self.assertEqual(muted["last_speaker_user_id"], self.student.pk)
            self.assertFalse(muted["streaming"])
            await ipad.disconnect()
            room = await database_sync_to_async(ClassroomMicrophone.objects.get)(pk=self.course.pk)
            self.assertTrue(room.receiver_channel)  # Old disconnect cannot clear new output.
            await sender.disconnect(); await laptop.disconnect()
        async_to_sync(run)()

    def test_mute_old_tokens_duplicate_frames_and_network_backlog(self):
        async def run():
            receiver, sender, stream = await self.pair()
            await self.command(receiver, "relay", generation=stream["generation"])
            await self.until(sender, lambda state: state.get("transport") == "relay")
            await self.drain(receiver); await self.drain(sender)
            await sender.send_to(bytes_data=self.packet(stream["generation"], sequence=1, age=1000))
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            packet = self.packet(stream["generation"], sequence=2)
            await sender.send_to(bytes_data=packet)
            self.assertEqual(await receiver.receive_from(), packet)
            await sender.send_to(bytes_data=packet)
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            muted = await self.command(receiver, "mute")
            await self.until(sender, lambda state: state.get("generation") == muted["generation"])
            self.assertEqual(muted["last_speaker_user_id"], self.student.pk)
            await self.drain(receiver); await self.drain(sender)
            await sender.send_to(bytes_data=self.packet(stream["generation"], sequence=3))
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            retry = await self.command(sender, "start", generation=stream["generation"])
            self.assertFalse(retry["this_sender"])
            self.assertNotEqual(muted["generation"], stream["generation"])
            await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_last_speaker_can_restart_after_stop_and_instructor_mute(self):
        async def run():
            receiver, sender, stream = await self.pair()
            stopped = await self.command(sender, "stop")
            self.assertFalse(stopped["streaming"])
            self.assertEqual(stopped["last_speaker_user_id"], self.student.pk)
            restarted = await self.command(sender, "start", generation=stopped["generation"])
            self.assertTrue(restarted["this_sender"])
            self.assertNotEqual(restarted["generation"], stream["generation"])
            muted = await self.command(receiver, "mute")
            self.assertFalse(muted["streaming"])
            self.assertEqual(muted["last_speaker_user_id"], self.student.pk)
            restarted = await self.command(sender, "start", generation=muted["generation"])
            self.assertTrue(restarted["this_sender"])
            await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_later_turns_start_on_the_relay_once_this_output_needed_it(self):
        async def run():
            receiver, sender, stream = await self.pair()
            self.assertEqual(stream["transport"], "direct")
            await self.command(receiver, "relay", generation=stream["generation"])
            await self.until(sender, lambda state: state.get("transport") == "relay")
            stopped = await self.command(sender, "stop")
            restarted = await self.command(sender, "start", generation=stopped["generation"])
            self.assertTrue(restarted["this_sender"])
            self.assertEqual(restarted["transport"], "relay")  # No second 5 s wait.
            # A different output device may be on a network that allows peers.
            laptop, connected, _ = await self.connect_as(self.instructor)
            await laptop.receive_json_from()
            moved = await self.command(laptop, "claim_output")
            self.assertEqual(moved["transport"], "direct")
            await self.drain(sender)
            restarted = await self.command(sender, "start", generation=moved["generation"])
            self.assertEqual(restarted["transport"], "direct")
            await sender.disconnect(); await receiver.disconnect(); await laptop.disconnect()
        async_to_sync(run)()

    def test_followup_expires_when_next_student_starts_and_old_audio_is_rejected(self):
        async def run():
            receiver, sender, stream = await self.pair()
            next_member = await database_sync_to_async(CourseMember.objects.get)(course=self.course, user=self.other)
            next_sender, connected, _ = await self.connect_as(self.other)
            self.assertTrue(connected)
            await next_sender.receive_json_from()
            await database_sync_to_async(select_microphone_student)(self.course.pk, next_member, self.lecture)
            chosen = await self.until(sender, lambda state: state.get("selected_user_id") == self.other.pk)
            self.assertFalse(chosen["this_sender"])
            self.assertEqual(chosen["last_speaker_user_id"], self.student.pk)
            followup = await self.command(sender, "start", generation=chosen["generation"])
            self.assertTrue(followup["this_sender"])
            self.assertEqual(followup["selected_user_id"], self.other.pk)
            next_turn = await self.command(next_sender, "start", generation=followup["generation"])
            self.assertTrue(next_turn["this_sender"])
            self.assertEqual(next_turn["last_speaker_user_id"], self.other.pk)
            disabled = await self.until(sender, lambda state: state.get("last_speaker_user_id") == self.other.pk)
            self.assertFalse(disabled["this_sender"])
            # The old speaker cannot restart even with the current generation.
            rejected = await self.command(sender, "start", generation=next_turn["generation"])
            self.assertFalse(rejected["this_sender"])
            self.assertEqual(rejected["generation"], next_turn["generation"])
            await self.command(receiver, "relay", generation=next_turn["generation"])
            await self.drain(receiver); await self.drain(sender); await self.drain(next_sender)
            await sender.send_to(bytes_data=self.packet(followup["generation"]))
            await sender.send_to(bytes_data=self.packet(next_turn["generation"], sequence=1))
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            packet = self.packet(next_turn["generation"], sequence=2)
            await next_sender.send_to(bytes_data=packet)
            self.assertEqual(await receiver.receive_from(), packet)
            await sender.disconnect(); await next_sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_revocation_and_expired_output_stop_audio(self):
        async def run():
            receiver, sender, stream = await self.pair()
            await database_sync_to_async(ClassroomMicrophone.objects.filter(pk=self.course.pk).update)(receiver_expires_at=timezone.now() - timedelta(seconds=1))
            state = await self.command(sender, "heartbeat")
            self.assertFalse(state["output_connected"])
            self.assertFalse(state["this_sender"])
            await database_sync_to_async(CourseMember.objects.filter(course=self.course, user=self.student).update)(active=False)
            await sender.send_json_to({"type": "heartbeat"})
            message = await sender.receive_output()
            # A pending state notification may arrive before the close.
            while message["type"] != "websocket.close":
                message = await sender.receive_output()
            self.assertEqual(message["code"], 4403)
            await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_turning_it_off_closes_open_sockets_and_refuses_new_ones(self):
        async def run():
            receiver, sender, stream = await self.pair()
            await database_sync_to_async(Course.objects.filter(pk=self.course.pk).update)(classroom_microphone=False)
            await database_sync_to_async(disable_classroom_microphone)(self.course.pk)
            for socket in (receiver, sender):
                message = await socket.receive_output()
                while message["type"] != "websocket.close":
                    message = await socket.receive_output()
                self.assertEqual(message["code"], 4403)
            for user in (self.instructor, self.student):
                await self.assert_refused(user)
            await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_bystanders_stop_and_heartbeat_without_writing(self):
        async def run():
            receiver, sender, stream = await self.pair()
            bystander, connected, _ = await self.connect_as(self.other)
            self.assertTrue(connected)
            await bystander.receive_json_from()
            before = await database_sync_to_async(ClassroomMicrophone.objects.get)(pk=self.course.pk)
            for action in ("stop", "heartbeat", "release_output", "relay", "mute"):
                state = await self.command(bystander, action, generation=stream["generation"])
                self.assertTrue(state["streaming"])
            after = await database_sync_to_async(ClassroomMicrophone.objects.get)(pk=self.course.pk)
            self.assertEqual(
                (before.generation, before.sender_channel, before.sender_expires_at, before.transport),
                (after.generation, after.sender_channel, after.sender_expires_at, after.transport),
            )
            await bystander.disconnect(); await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_a_flood_of_messages_is_cut_off(self):
        async def run():
            socket, connected, _ = await self.connect_as(self.other)
            self.assertTrue(connected)
            await socket.receive_json_from()
            for _ in range(60):
                await socket.send_json_to({"type": "heartbeat"})
            message = await socket.receive_output(timeout=5)
            while message["type"] != "websocket.close":
                message = await socket.receive_output(timeout=5)
            self.assertEqual(message["code"], 4400)
            await socket.disconnect()
        async_to_sync(run)()

    def test_relay_frames_do_not_query_per_frame(self):
        async def run():
            receiver, sender, stream = await self.pair()
            await self.command(receiver, "relay", generation=stream["generation"])
            await self.until(sender, lambda state: state.get("transport") == "relay")
            await self.drain(receiver); await self.drain(sender)
            from peer_lecture import microphone
            calls = []
            original = microphone._destination
            async def counting(*args, **kwargs):
                calls.append(args)
                return await original(*args, **kwargs)
            microphone._destination = counting
            try:
                for sequence in range(10):
                    packet = self.packet(stream["generation"], sequence=sequence)
                    await sender.send_to(bytes_data=packet)
                    self.assertEqual(await receiver.receive_from(), packet)
            finally:
                microphone._destination = original
            # One lookup per end, not one per end per frame.
            self.assertLessEqual(len(calls), 2)
            await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_reselection_changes_generation_even_for_the_same_student(self):
        async def run():
            receiver, sender, stream = await self.pair()
            await database_sync_to_async(select_microphone_student)(self.course.pk, self.member, self.lecture)
            state = await self.until(sender, lambda state: state.get("generation") != stream["generation"])
            self.assertFalse(state["this_sender"])
            self.assertEqual(state["selected_user_id"], self.student.pk)
            await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_receiver_permission_revocation_drops_audio_before_next_heartbeat(self):
        async def run():
            receiver, sender, stream = await self.pair()
            await self.command(receiver, "relay", generation=stream["generation"])
            await self.until(sender, lambda state: state.get("transport") == "relay")
            await self.drain(receiver); await self.drain(sender)
            await database_sync_to_async(CourseMember.objects.filter(course=self.course, user=self.instructor).update)(role="student")
            await sender.send_to(bytes_data=self.packet(stream["generation"]))
            self.assertTrue(await receiver.receive_nothing(timeout=0.05))
            await sender.disconnect(); await receiver.disconnect()
        async_to_sync(run)()

    def test_course_superuser_and_site_superuser_can_control_output(self):
        async def run():
            await database_sync_to_async(CourseMember.objects.filter(course=self.course, user=self.instructor).update)(role="superuser")
            administrator = await database_sync_to_async(User.objects.create_superuser)("mic-admin", password="pw")
            for user in [self.instructor, administrator]:
                socket, connected, _ = await self.connect_as(user)
                self.assertTrue(connected)
                await socket.receive_json_from()
                state = await self.command(socket, "claim_output")
                self.assertTrue(state["this_output"])
                await socket.disconnect()
        async_to_sync(run)()
