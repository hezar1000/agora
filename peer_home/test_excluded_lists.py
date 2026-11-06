"""A list outside included_lists is recorded but scores nothing.

Speaking from an excluded list still counts as having spoken -- it drives
selection weighting and the "already spoken" view -- but must not earn points
or feed the participation calculation. Ported from the deployed instance to
hold this branch's own implementation to the same behaviour. The selection
endpoints are POST-only here, so the calls differ from the originals even
though the behaviour asserted does not.
"""

from datetime import timedelta
from unittest.mock import patch

import pytz
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from peer_course.models import Course, CourseMember, CourseParticipation, CourseSession

# Selection broadcasts over Channels. Without this the tests reach for a
# real Redis, which passes on a developer machine that happens to run one
# and fails in CI, which does not.
IN_MEMORY_CHANNELS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class ExcludedParticipationListTests(TestCase):
    def setUp(self):
        self.instructor_user = User.objects.create_user(
            username="excluded-list-instructor", password="testpass123"
        )
        self.student_user = User.objects.create_user(
            username="excluded-list-student", password="testpass123"
        )
        self.other_student_user = User.objects.create_user(
            username="excluded-list-other-student", password="testpass123"
        )
        # List 1 is deliberately absent: it is the excluded one.
        self.course = Course.objects.create(
            displayname="Excluded List Test",
            browsable=True,
            archived=False,
            hand_raise_point=1,
            speaking_point=2,
            # MultiSelectField stores strings; list_counts_for_points
            # coerces, which is why an int-keyed lookup still resolves.
            included_lists=["2", "3", "4"],
        )
        CourseMember.objects.create(
            course=self.course, user=self.instructor_user, role="instructor"
        )
        self.student = CourseMember.objects.create(
            course=self.course, user=self.student_user, role="student", hand_up=True
        )
        self.other_student = CourseMember.objects.create(
            course=self.course,
            user=self.other_student_user,
            role="student",
            hand_up=True,
        )

        course_tz = pytz.timezone(self.course.course_timezone)
        local_now = timezone.localtime(timezone.now(), course_tz)
        self.session = CourseSession.objects.create(
            course=self.course,
            is_template=False,
            # The window is dated from its start, not from now. Taking the
            # date from `now` while taking the start time from an hour earlier
            # wraps past midnight between 00:00 and 01:00 -- the session then
            # claimed to start at 23:0x tonight, was not active, and these
            # tests failed for the first hour of every day.
            session_date=(local_now - timedelta(hours=1)).date(),
            start_time_pst=(local_now - timedelta(hours=1)).time(),
            end_time_pst=(local_now + timedelta(hours=1)).time(),
        )

        self.client.login(
            username=self.instructor_user.username, password="testpass123"
        )
        client_session = self.client.session
        client_session["course_id"] = self.course.id
        client_session.save()

    def test_membership_is_exact_rather_than_a_substring(self):
        self.course.refresh_from_db()

        self.assertFalse(self.course.list_counts_for_points(1))
        self.assertTrue(self.course.list_counts_for_points(2))

    def test_a_speaker_on_an_excluded_list_is_not_counted(self):
        """The speaker row must follow the list, like every other row.

        It previously hardcoded count_in_calculations=True while the
        non-speaker rows beside it followed the list, so speaking on an
        excluded list still marked the speaker as having spoken and removed
        them from the unspoken pool.
        """
        response = self.client.post(
            f"/choose_next/{self.student.id}/1/", {"selection_version": 0}
        )

        self.assertEqual(response.status_code, 200)
        speaker = CourseParticipation.objects.get(
            participant=self.student, session=self.session, spoke_upon_participation=True
        )
        self.assertEqual(speaker.participation_list, 1)
        self.assertFalse(speaker.count_in_calculations)
        self.assertEqual(speaker.participation_points_gained, 0)

    def test_manual_choice_records_an_excluded_list_without_scoring_it(self):
        response = self.client.post(
            f"/choose_next/{self.student.id}/1/", {"selection_version": 0}
        )

        self.assertEqual(response.status_code, 200)
        participations = CourseParticipation.objects.filter(session=self.session)
        self.assertTrue(participations.filter(spoke_upon_participation=True).exists())
        self.assertFalse(participations.filter(count_in_calculations=True).exists())
        self.assertFalse(
            participations.exclude(participation_points_gained=0).exists()
        )

    def test_an_included_list_does_score(self):
        """The contrast case, so the assertions above cannot pass vacuously."""
        self.student.hand_up = False
        self.student.hand_up_list_2 = True
        self.student.save(update_fields=["hand_up", "hand_up_list_2"])

        response = self.client.post(
            f"/choose_next/{self.student.id}/2/", {"selection_version": 0}
        )

        self.assertEqual(response.status_code, 200)
        scored = CourseParticipation.objects.filter(
            session=self.session, participation_list=2, count_in_calculations=True
        )
        self.assertTrue(scored.exists())
