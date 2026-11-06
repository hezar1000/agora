"""Minimal process and dependency health checks for Agora operators."""

import asyncio
import uuid

from asgiref.sync import async_to_sync
from channels.layers import channel_layers
from django.db import connection
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


CHANNEL_CHECK_TIMEOUT_SECONDS = 2


def _plain_status(body, status=200):
    return HttpResponse(
        f"{body}\n",
        status=status,
        content_type="text/plain; charset=utf-8",
    )


def _database_is_ready():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
        return bool(result and result[0] == 1)
    except Exception:
        # Probes intentionally expose no driver, host, credential, or schema detail.
        return False


async def _channel_round_trip(layer):
    marker = uuid.uuid4().hex
    channel_name = await asyncio.wait_for(
        layer.new_channel("health."),
        timeout=CHANNEL_CHECK_TIMEOUT_SECONDS,
    )
    await asyncio.wait_for(
        layer.send(
            channel_name,
            {"type": "health.check", "marker": marker},
        ),
        timeout=CHANNEL_CHECK_TIMEOUT_SECONDS,
    )
    event = await asyncio.wait_for(
        layer.receive(channel_name),
        timeout=CHANNEL_CHECK_TIMEOUT_SECONDS,
    )
    return event == {"type": "health.check", "marker": marker}


async def _isolated_channel_round_trip():
    # The process-wide layer can have many long-lived receive coroutines for
    # classroom sockets. Running a readiness receive through that same layer
    # makes the probe compete for its receive lock and intermittently report
    # Redis as down under perfectly normal traffic. A short-lived backend
    # verifies the configured service independently and is then closed.
    layer = channel_layers.make_backend("default")
    try:
        return await _channel_round_trip(layer)
    finally:
        close_pools = getattr(layer, "close_pools", None)
        if close_pools is not None:
            await close_pools()


def _channel_layer_is_ready():
    try:
        return bool(async_to_sync(_isolated_channel_round_trip)())
    except Exception:
        # Redis/backend connection details must not be returned to callers.
        return False


@never_cache
@require_safe
def live(request):
    """Confirm only that this Django worker can answer an HTTP request."""
    return _plain_status("ok")


@never_cache
@require_safe
def ready(request):
    """Confirm that the database and configured Channels backend are usable."""
    if not _database_is_ready() or not _channel_layer_is_ready():
        return _plain_status("unavailable", status=503)
    return _plain_status("ready")
