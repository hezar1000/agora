"""ASGI entry point for Agora's HTTP and WebSocket traffic."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mta2.settings")

# Initialize Django before importing routing modules that load model classes.
from django.core.asgi import get_asgi_application  # noqa: E402

django_asgi_application = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from peer_lecture.routing import websocket_urlpatterns  # noqa: E402
from peer_account.middleware import RequireSecondFactorASGIMiddleware  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_application,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                RequireSecondFactorASGIMiddleware(
                    URLRouter(websocket_urlpatterns)
                )
            )
        ),
    }
)
