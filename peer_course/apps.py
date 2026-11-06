from django.apps import AppConfig


class PeerCourseConfig(AppConfig):
    name = "peer_course"

    def ready(self):
        # Connects the hand-raise recorder. Imported for its side effect: the
        # receivers register on import, and without this nothing records.
        from . import hand_events  # noqa: F401
