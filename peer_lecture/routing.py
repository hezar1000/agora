# peer_lecture/routing.py
from django.urls import re_path
from .consumers import WebsocketConsumer
from .microphone import MicrophoneConsumer

websocket_urlpatterns = [
    re_path(r'^ws/(?P<course_id>[0-9]+)/microphone/$', MicrophoneConsumer.as_asgi()),
    re_path(r'^ws/(?P<course_id>[0-9]+)/$', WebsocketConsumer.as_asgi()),
]
