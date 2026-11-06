"""WSGI compatibility entry point for Agora.

Production uses ASGI because Agora relies on WebSockets. This entry point is
kept for management tooling and HTTP-only integrations.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mta2.settings")

application = get_wsgi_application()
