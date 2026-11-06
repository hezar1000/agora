"""Authenticated, ephemeral classroom audio. No audio is stored or recorded.

The database owns permissions and playback-device leases; Channels routes
WebRTC signaling and fallback PCM only to the current pair. Direct audio avoids
the server round trip; the relay works when campus Wi-Fi blocks peer connections.
"""

import json
import logging
import re
import math
import struct
import time
import uuid
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.exceptions import ChannelFull
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.contrib.auth import get_user_model

from peer_course.models import Course, CourseMember, INSTRUCTOR_ROLES, LEARNER_ROLES
from .consumers import _same_origin
from .models import ClassroomMicrophone, Lecture

# Heartbeats come every 5 s, but a browser throttles timers on a page hidden
# behind slides to about once a minute. A dropped socket releases its lease at
# once (see _release), so the lease only has to outlive a throttled timer.
LEASE = timedelta(seconds=75)
TEXT_BURST = 40  # ICE candidates arrive in a burst when a call starts.
TEXT_RATE = 10   # Sustained messages per second; heartbeats are one per 5 s.
ROUTE_TTL = 1.0  # Seconds a cached audio route is trusted between state changes.
HEADER = struct.Struct("!16sIdI")  # generation, sequence, server-clock milliseconds, Hz
SAMPLE_RATES = {16000, 24000, 44100, 48000}
MAX_AGE_MS = 120
AUDIO_LOG = logging.getLogger("agora.events")

def _audio_log(course_id, generation, event, **fields):
    AUDIO_LOG.info("classroom_audio %s", json.dumps({
        "course": course_id, "turn": str(generation), "event": event, **fields,
    }, separators=(",", ":"), allow_nan=False))


def _diagnostic_report(data):
    """Only bounded numeric measurements and fixed labels may enter logs."""
    if not isinstance(data, dict):
        return {}
    clean = {}
    for key in ("bufferMs", "peakBufferMs", "frameAgeMs", "roundTripMs", "clockRttMs",
                "captureMs", "graphMs", "frameLagMs", "outputMs", "received",
                "discarded", "gaps", "underruns", "skipped", "expired", "senderDropped",
                "cuts", "levelDb", "reductionDb", "sendQueueBytes"):
        value = data.get(key)
        if type(value) in (int, float) and math.isfinite(value) and -100 <= value <= 1e9:
            clean[key] = round(value, 2)
    for key, allowed in {
        "event": {"periodic", "connected", "feedback", "stop", "manual_relay", "legacy_peer",
                  "connection_failed", "disconnected", "connect_timeout", "channel_closed", "send_backlog"},
        "browser": {"firefox", "edge", "chromium", "safari", "other"},
        "context": {"running", "suspended", "closed", "interrupted"},
    }.items():
        value = data.get(key)
        if isinstance(value, str) and value in allowed:
            clean[key] = value
    if type(data.get("hidden")) is bool:
        clean["hidden"] = data["hidden"]
    if isinstance(data.get("version"), str) and re.fullmatch(r"[0-9]{8}[a-z]", data["version"]):
        clean["version"] = data["version"]
    return clean



def _notify(course_id):
    transaction.on_commit(lambda: async_to_sync(get_channel_layer().group_send)(
        f"microphone_{course_id}", {"type": "microphone_state"},
    ))


def _invalidate(room):
    if room.sender_channel:
        _audio_log(room.course_id, room.generation, "turn_end", transport=room.transport)
    room.generation = uuid.uuid4()
    room.sender_channel = ""
    room.sender_expires_at = None
    room.transport = "relay" if room.relay_preferred else "direct"


def _drop_receiver(room):
    room.receiver_channel = ""
    room.receiver_user = None
    room.receiver_expires_at = None
    room.relay_preferred = False
    _invalidate(room)


def select_microphone_student(course_id, member, lecture):
    """Called inside the course's selection transaction, including ungraded queues."""
    if not Course.objects.filter(pk=course_id, classroom_microphone=True).exists():
        return
    room, _ = ClassroomMicrophone.objects.get_or_create(course_id=course_id)
    if lecture is None or room.lecture_id != lecture.pk:
        room.last_speaker = None
    room.selected_member = member if lecture else None
    room.lecture = lecture
    _invalidate(room)
    room.save()
    _notify(course_id)


def end_microphone_lecture(lecture_id):
    room = ClassroomMicrophone.objects.filter(lecture_id=lecture_id).first()
    if room is None:
        return
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=room.course_id)
        room = ClassroomMicrophone.objects.select_for_update().get(pk=room.pk)
        if room.lecture_id != lecture_id:
            return
        room.selected_member = None
        room.last_speaker = None
        _invalidate(room)
        room.save()
        _notify(room.course_id)


def disable_classroom_microphone(course_id):
    """The course turned the feature off: drop every permission and lease.

    The notification makes each open socket recheck its role, which is now
    refused, so they all close and no further audio is routed.
    """
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=course_id)
        room = ClassroomMicrophone.objects.select_for_update().filter(pk=course_id).first()
        if room is None:
            return
        room.selected_member = None
        room.last_speaker = None
        _drop_receiver(room)
        room.save()
        _notify(course_id)


def _role(user, course_id):
    if not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser and get_user_model().objects.filter(pk=user.pk, is_superuser=True, is_active=True).exists() and Course.objects.filter(pk=course_id, archived=False, classroom_microphone=True).exists():
        return "instructor"
    return CourseMember.objects.filter(
        course_id=course_id, course__archived=False, course__classroom_microphone=True,
        user_id=user.pk, user__is_active=True, active=True,
    ).values_list("role", flat=True).first()


def _eligible(room, member):
    return bool(
        room.lecture and room.lecture.end_time is None
        and member and member.course_id == room.course_id
        and member.active and member.user.is_active and member.role in LEARNER_ROLES
    )


def _name(member):
    return member.get_user_fullname() or member.user.username


def _snapshot(room, channel):
    now = timezone.now()
    selected = room.selected_member
    active = bool(room.lecture and room.lecture.end_time is None)
    selected_eligible = _eligible(room, selected)
    last = room.last_speaker
    last_eligible = _eligible(room, last)
    receiving = bool(room.receiver_channel and room.receiver_expires_at and room.receiver_expires_at > now)
    sending = bool(last_eligible and receiving and room.sender_channel and room.sender_expires_at and room.sender_expires_at > now)
    return {
        "type": "state", "generation": str(room.generation), "active": active,
        "transport": room.transport, "diagnostics": 1,
        "selected_user_id": selected.user_id if selected_eligible else None,
        "selected_name": _name(selected) if selected_eligible else "",
        "last_speaker_user_id": last.user_id if last_eligible else None,
        "last_speaker_name": _name(last) if last_eligible else "",
        "output_connected": receiving, "streaming": sending,
        "this_output": receiving and room.receiver_channel == channel,
        "this_sender": sending and room.sender_channel == channel,
        "server_time": time.time() * 1000,
    }


def _may_change(room, role, user, channel, action):
    """Whether an action could alter the room, judged without the lock.

    Anything it rules out would be a no-op under the lock as well; the locked
    path still rechecks everything that this lets through. Expired leases are
    already reported as gone by _snapshot, so they need no write here either.
    """
    if action is None:
        return False
    if action in {"claim_output", "mute"}:
        return role in INSTRUCTOR_ROLES
    if action == "start":
        return role in LEARNER_ROLES and user.pk in {
            room.selected_member and room.selected_member.user_id,
            room.last_speaker and room.last_speaker.user_id,
        }
    # stop, release_output, heartbeat, relay: only the two endpoints hold
    # anything to give up or renew.
    return channel in {room.sender_channel, room.receiver_channel}


@database_sync_to_async
def _state(user, course_id, channel, action=None, generation=None):
    role = _role(user, course_id)
    if role is None:
        return None
    room = ClassroomMicrophone.objects.select_related(
        "lecture", "selected_member__user__profile", "last_speaker__user__profile",
    ).filter(pk=course_id).first()
    if room is not None and not _may_change(room, role, user, channel, action):
        # A whole lecture receives these notifications, and every phone's tab
        # switch reports a stop. Passive students must not serialize hundreds
        # of writes on the course's selection lock.
        return _snapshot(room, channel)
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=course_id)
        room, created = ClassroomMicrophone.objects.get_or_create(course_id=course_id)
        now = timezone.now()
        changed = changed_lease = False
        if room.receiver_channel and (not room.receiver_expires_at or room.receiver_expires_at <= now):
            _drop_receiver(room)
            changed = True
        if room.sender_channel and (not room.sender_expires_at or room.sender_expires_at <= now):
            _invalidate(room)
            changed = True
        instructor = role in INSTRUCTOR_ROLES
        if action == "claim_output" and instructor:
            if room.receiver_channel != channel:
                room.relay_preferred = False
                _invalidate(room)
                changed = True
            room.receiver_channel = channel
            room.receiver_user = user
            room.receiver_expires_at = now + LEASE
        elif action == "release_output" and room.receiver_channel == channel:
            _drop_receiver(room)
            changed = True
        elif action == "mute" and instructor:
            # Pause audio without taking away the speaker's follow-up turn.
            _invalidate(room)
            changed = True
        elif action == "start" and role in LEARNER_ROLES and str(room.generation) == generation:
            speaker = next((
                member for member in (room.selected_member, room.last_speaker)
                if _eligible(room, member) and member.user_id == user.pk
            ), None)
            if speaker and room.receiver_channel:
                # A second tab cannot publish alongside the first one.
                _invalidate(room)
                # Starting the next student's audio ends the prior speaker's
                # follow-up permission, even if their browser still has a button.
                room.last_speaker = speaker
                room.sender_channel = channel
                room.sender_expires_at = now + LEASE
                changed = True
        elif action in {"stop", "disconnect"} and room.sender_channel == channel:
            _invalidate(room)
            changed = True
        elif action == "relay" and str(room.generation) == generation and channel in {room.sender_channel, room.receiver_channel} and room.sender_channel:
            room.transport = "relay"
            room.relay_preferred = True
            changed = True
        if action == "heartbeat":
            if room.receiver_channel == channel:
                room.receiver_expires_at = now + LEASE
                changed_lease = True
            if room.sender_channel == channel:
                room.sender_expires_at = now + LEASE
                changed_lease = True
        if action == "disconnect" and room.receiver_channel == channel:
            _drop_receiver(room)
            changed = True
        if changed or changed_lease or created or action == "claim_output":
            room.save()
        if changed or action == "claim_output":
            _audio_log(course_id, room.generation, "control", action=action or "lease_expired",
                       transport=room.transport, sending=bool(room.sender_channel), receiving=bool(room.receiver_channel))
        if changed:
            _notify(course_id)
        return _snapshot(room, channel)


def _live_pair(course_id, generation):
    """Fresh permissions for both endpoints, also after cross-worker routing."""
    now = timezone.now()
    instructors = CourseMember.objects.filter(
        course_id=OuterRef("course_id"), user_id=OuterRef("receiver_user_id"),
        active=True, role__in=INSTRUCTOR_ROLES,
    )
    return ClassroomMicrophone.objects.annotate(
        receiver_is_instructor=Exists(instructors),
    ).filter(
        Q(receiver_is_instructor=True) | Q(receiver_user__is_superuser=True),
        course_id=course_id, course__archived=False, course__classroom_microphone=True,
        generation=generation,
        receiver_user__is_active=True,
        sender_expires_at__gt=now, receiver_expires_at__gt=now,
        last_speaker__active=True, last_speaker__user__is_active=True,
        last_speaker__role__in=LEARNER_ROLES, lecture__end_time__isnull=True,
        lecture__isnull=False,
    )


@database_sync_to_async
def _destination(course_id, generation, sender, transport=None):
    query = _live_pair(course_id, generation).filter(sender_channel=sender)
    if transport:
        query = query.filter(transport=transport)
    return query.values_list("receiver_channel", flat=True).first()


@database_sync_to_async
def _signal_destination(course_id, generation, channel):
    room = _live_pair(course_id, generation).filter(transport="direct").first()
    if room:
        if channel == room.sender_channel:
            return room.receiver_channel
        if channel == room.receiver_channel:
            return room.sender_channel


class MicrophoneConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        self.course_id = int(self.scope["url_route"]["kwargs"]["course_id"])
        state = None
        if self.user is not None and _same_origin(self.scope):
            state = await _state(self.user, self.course_id, self.channel_name)
        if state is None:
            # Closing before accept reaches the browser as a bare 1006, which
            # it would retry forever; an accepted close carries the 4403 that
            # tells it to stop.
            await self.accept()
            return await self.close(code=4403)
        self.group = f"microphone_{self.course_id}"
        self.last_sequence = -1
        self.packet_generation = None
        self.tokens = 75.0
        self.refill = time.monotonic()
        self.text_tokens = float(TEXT_BURST)
        self.text_refill = self.refill
        self.routes = {}
        self.diagnostic_at = -math.inf
        self.relay_counts = {"received": 0, "stale": 0, "congested": 0}
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send(text_data=json.dumps(state))

    async def disconnect(self, code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)
            # Revoked members still need to relinquish their device lease.
            await database_sync_to_async(self._release)(code)

    def _release(self, close_code=None):
        held = ClassroomMicrophone.objects.filter(pk=self.course_id).filter(
            Q(sender_channel=self.channel_name) | Q(receiver_channel=self.channel_name),
        ).exists()
        if not held:
            return  # Most sockets never held anything; skip the course lock.
        with transaction.atomic():
            Course.objects.select_for_update().filter(pk=self.course_id).first()
            room = ClassroomMicrophone.objects.filter(pk=self.course_id).first()
            if not room:
                return
            changed = False
            if room.sender_channel == self.channel_name:
                _invalidate(room)
                changed = True
            if room.receiver_channel == self.channel_name:
                _drop_receiver(room)
                changed = True
            if changed:
                _audio_log(self.course_id, room.generation, "disconnect", close_code=close_code)
                room.save()
                _notify(self.course_id)

    async def microphone_state(self, event):
        # Any change -- mute, takeover, new turn, lecture end -- invalidates
        # routes on every worker before the next frame is forwarded.
        self.routes = {}
        state = await _state(self.user, self.course_id, self.channel_name)
        if state is None:
            return await self.close(code=4403)
        await self.send(text_data=json.dumps(state))

    async def microphone_signal(self, event):
        if await _signal_destination(self.course_id, event["generation"], event["sender"]) == self.channel_name:
            await self.send(text_data=json.dumps({
                "type": "signal", "generation": event["generation"],
                "signal": event["signal"],
            }))

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            return await self._audio(bytes_data)
        if not text_data or len(text_data) > 65536:
            return await self.close(code=4400)
        now = time.monotonic()
        self.text_tokens = min(TEXT_BURST, self.text_tokens + (now - self.text_refill) * TEXT_RATE)
        self.text_refill = now
        if self.text_tokens < 1:
            return await self.close(code=4400)  # No real client sends this fast.
        self.text_tokens -= 1
        try:
            message = json.loads(text_data)
            if not isinstance(message, dict):
                raise ValueError()
            action = message.get("type")
            if action not in {"claim_output", "release_output", "mute", "start", "stop", "heartbeat", "relay", "signal", "diagnostics"}:
                raise ValueError()
        except (ValueError, TypeError):
            return await self.close(code=4400)
        if action == "diagnostics":
            # New frontends only send this after the capability flag in state.
            # Do not trust client role, transport, identifiers, or arbitrary text.
            if len(text_data) > 4096 or now - self.diagnostic_at < 1:
                return
            self.diagnostic_at = now
            try:
                generation = uuid.UUID(message.get("generation", ""))
            except (ValueError, TypeError, AttributeError):
                return
            def endpoint():
                if _role(self.user, self.course_id) is None:
                    return None
                room = _live_pair(self.course_id, generation).first()
                if room and self.channel_name in {room.sender_channel, room.receiver_channel}:
                    return ("sender" if self.channel_name == room.sender_channel else "receiver", room.transport)
            pair = await database_sync_to_async(endpoint)()
            if pair:
                _audio_log(self.course_id, generation, "metrics", role=pair[0], transport=pair[1],
                           client=_diagnostic_report(message.get("report")), relay=self.relay_counts)
            return
        if action == "signal":
            try:
                generation = str(uuid.UUID(message.get("generation", "")))
            except (ValueError, TypeError, AttributeError):
                return await self.close(code=4400)
            signal = message.get("signal")
            if not isinstance(signal, dict) or set(signal) not in ({"description"}, {"candidate"}):
                return await self.close(code=4400)
            if await database_sync_to_async(_role)(self.user, self.course_id) is None:
                return await self.close(code=4403)
            recipient = await _signal_destination(self.course_id, generation, self.channel_name)
            if recipient:
                try:
                    await self.channel_layer.send(recipient, {
                        "type": "microphone_signal", "generation": generation,
                        "sender": self.channel_name, "signal": signal,
                    })
                except ChannelFull:
                    pass
            return
        state = await _state(
            self.user, self.course_id, self.channel_name, action,
            message.get("generation"),
        )
        if state is None:
            return await self.close(code=4403)
        state["reply_to"] = action
        state["ping"] = message.get("ping")
        await self.send(text_data=json.dumps(state))

    async def _audio(self, data):
        if len(data) < HEADER.size or len(data) > HEADER.size + 1920:
            return await self.close(code=4400)
        raw_generation, sequence, captured, rate = HEADER.unpack_from(data)
        if rate not in SAMPLE_RATES or len(data) != HEADER.size + rate // 50 * 2 or not math.isfinite(captured):
            return await self.close(code=4400)
        self.relay_counts["received"] += 1
        age = time.time() * 1000 - captured
        if age > MAX_AGE_MS or age < -100:
            self.relay_counts["stale"] += 1
            return  # A network backlog is discarded, never replayed.
        now = time.monotonic()
        self.tokens = min(75, self.tokens + (now - self.refill) * 50)
        self.refill = now
        if self.tokens < 1:
            return
        self.tokens -= 1
        generation = uuid.UUID(bytes=raw_generation)
        if generation != self.packet_generation:
            self.packet_generation = generation
            self.last_sequence = -1
        if sequence <= self.last_sequence:
            return
        self.last_sequence = sequence
        receiver = await self._route(generation, self.channel_name)
        if not receiver:
            return
        try:
            await self.channel_layer.send(receiver, {
                "type": "microphone_audio", "data": data,
                "sender": self.channel_name, "generation": str(generation),
            })
        except ChannelFull:
            self.relay_counts["congested"] += 1  # Live audio is expendable; don't queue it behind a slow device.

    async def _route(self, generation, sender):
        """The relay receiver for a sender, cached for at most ROUTE_TTL.

        Querying per 20 ms frame put two joins a frame on the worker's single
        database thread. State changes clear the cache (microphone_state), so
        a revocation is seen at once; the TTL bounds anything changed in the
        database without a notification.
        """
        key = (generation, sender)
        now = time.monotonic()
        cached = self.routes.get(key)
        if cached and now - cached[1] < ROUTE_TTL:
            return cached[0]
        receiver = await _destination(self.course_id, generation, sender, "relay")
        self.routes = {key: (receiver, now)}
        return receiver

    async def microphone_audio(self, event):
        data = event["data"]
        if time.time() * 1000 - HEADER.unpack_from(data)[2] > MAX_AGE_MS:
            return
        # Recheck after routing: mute/takeover/end may have committed meanwhile.
        receiver = await self._route(uuid.UUID(event["generation"]), event["sender"])
        if receiver == self.channel_name:
            await self.send(bytes_data=data)
