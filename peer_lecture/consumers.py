import json
import logging
from time import monotonic
from urllib.parse import urlsplit

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from peer_course.models import CourseMember


eventLogger = logging.getLogger("agora.events")


def _same_origin(scope):
    """Cookies authenticate sockets, so browser connections must be same-origin."""
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    origin_raw = headers.get(b"origin")
    host_raw = headers.get(b"host")
    if not origin_raw or not host_raw:
        return False
    try:
        origin = urlsplit(origin_raw.decode("ascii"))
        host = host_raw.decode("ascii").lower()
    except (UnicodeDecodeError, ValueError):
        return False
    return origin.scheme in {"http", "https"} and origin.netloc.lower() == host


@database_sync_to_async
def _may_join_course(user_id, course_id, is_superuser):
    if is_superuser:
        return True
    return CourseMember.objects.filter(
        user_id=user_id,
        course_id=course_id,
        active=True,
    ).exists()


class WebsocketConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        try:
            self.course_id = int(self.scope["url_route"]["kwargs"]["course_id"])
        except (KeyError, TypeError, ValueError):
            return await self.close(code=4400)

        if (
            user is None
            or not user.is_authenticated
            or not _same_origin(self.scope)
            or not await _may_join_course(user.pk, self.course_id, user.is_superuser)
        ):
            return await self.close(code=4403)

        self.user_id = user.pk
        self.is_superuser = user.is_superuser
        self.membership_checked_at = monotonic()
        self.room_group_name = f"course_{self.course_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()
        eventLogger.info("User %s connected to course %s live updates", self.user_id, self.course_id)

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
            eventLogger.info("User %s disconnected from live updates", self.user_id)

    async def receive(self, text_data=None, bytes_data=None):
        # Live clients receive server-originated notifications only.
        return None

    async def send_message(self, event):
        # Membership can be revoked while a long-lived socket is open. Revalidate
        # on every bounded server event so deactivated users are removed before
        # the event payload is delivered.
        try:
            revalidation_seconds = max(
                0,
                float(
                    getattr(
                        settings,
                        "WEBSOCKET_MEMBERSHIP_REVALIDATION_SECONDS",
                        5,
                    )
                ),
            )
        except (TypeError, ValueError):
            revalidation_seconds = 5
        if monotonic() - self.membership_checked_at >= revalidation_seconds:
            if not await _may_join_course(
                self.user_id,
                self.course_id,
                self.is_superuser,
            ):
                await self.channel_layer.group_discard(
                    self.room_group_name,
                    self.channel_name,
                )
                await self.close(code=4403)
                return
            self.membership_checked_at = monotonic()

        # All producers use the authenticated Django User primary key here (not a
        # CourseMember primary key or a browser-supplied identifier).
        if str(event.get("send_auth_id")) == str(self.user_id):
            return
        try:
            await self.send(text_data=json.dumps({
                "key": event["key"],
                "value": event.get("value"),
            }))
        except (KeyError, TypeError, ValueError):
            eventLogger.warning(
                "Rejected malformed live-update event for course %s",
                self.course_id,
            )
            await self.close(code=4400)
