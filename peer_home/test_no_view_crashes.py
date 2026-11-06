"""No page may raise, for any role, however it is reached.

Two export routes raised TypeError for everyone who opened them, instructors
included: the URLconf registered a second path segment that the view did not
accept. Nothing linked to those URLs, so nothing noticed.

This enumerates the URLconf rather than a list someone maintains, so a page
nobody links to is still covered.
"""

import re

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import get_resolver

from peer_course.base import CourseBase
from peer_course.models import CourseMember

IN_MEMORY_CHANNELS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


def _concrete(pattern, ids):
    p = pattern.replace("^", "").replace("$", "")
    # Older course routes use a positional course ID instead of a named group.
    p = p.replace("course/([0-9]+)/", f"course/{ids['cid']}/", 1)
    p = re.sub(r"\(\?P<([a-z_]+)>[^)]*\)", lambda m: ids.get(m.group(1), "1"), p)
    p = re.sub(r"\([^)]*\)", "1", p)
    p = p.replace("\\.", ".").replace("\\", "")
    if any(ch in p for ch in "[]+*?|"):
        return None
    return "/" + p.lstrip("/")


def _walk(resolver, prefix=""):
    for entry in resolver.url_patterns:
        pat = prefix + str(entry.pattern)
        if hasattr(entry, "url_patterns"):
            yield from _walk(entry, pat)
        else:
            yield pat


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    CHANNEL_LAYERS=IN_MEMORY_CHANNELS,
)
class NoViewCrashesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(username="crash_instructor")
        cls.course = CourseBase.create(
            cls.instructor, "Crash audit", browsable=True, archived=False, on_list=True
        )
        CourseMember.objects.create(
            course=cls.course, user=cls.instructor, role="instructor", active=True
        )
        cls.ta = User.objects.create_user(username="crash_ta")
        CourseMember.objects.create(course=cls.course, user=cls.ta, role="ta", active=True)
        cls.student = User.objects.create_user(username="crash_student")
        CourseMember.objects.create(
            course=cls.course, user=cls.student, role="student", active=True
        )

    def _client(self, user):
        client = Client()
        client.force_login(user)
        session = client.session
        session["course_id"] = self.course.id
        session.save()
        return client

    def test_no_url_raises_for_any_role(self):
        # PostgreSQL sequences keep advancing between tests, even on rollback.
        ids = {"cid": str(self.course.pk), "course_id": str(self.course.pk),
               "uid": str(self.instructor.pk)}
        urls = sorted(
            {u for u in (_concrete(p, ids) for p in _walk(get_resolver()))
             if u and not u.startswith("/admin")}
        )
        self.assertGreater(len(urls), 40, "the URLconf walk found almost nothing")

        failures = []
        for label, user in (("student", self.student), ("ta", self.ta),
                            ("instructor", self.instructor)):
            client = self._client(user)
            for url in urls:
                try:
                    response = client.get(url)
                except Exception as exc:  # noqa: BLE001 - reporting, not handling
                    failures.append(f"{label} {url}: {type(exc).__name__}: {exc}")
                    continue
                if response.status_code == 500:
                    failures.append(f"{label} {url}: 500")
        self.assertEqual(failures, [], "pages raised:\n  " + "\n  ".join(failures))
