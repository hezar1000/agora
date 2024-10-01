from django.apps import AppConfig
from django.db.backends.signals import connection_created


class PeerLectureConfig(AppConfig):
    name = 'peer_lecture'