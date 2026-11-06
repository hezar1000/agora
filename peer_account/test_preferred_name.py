"""The name a student is called, and whether it survives the next sign-in.

The edit page has always let a student change First name and Last name, and
it has never worked: custom_saml_acs rewrites first_name and last_name from
the SAML assertion on every single CWL sign-in. So a student could set their
name, see it take effect, and have it revert at the start of the next
lecture -- on the screen the instructor reads names off.

A preferred name lives on the profile, which nothing overwrites.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from peer_account.models import Profile
from peer_course.base import CourseBase
from peer_course.models import CourseMember


class DisplayNameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "12345678", first_name="Jonathan", last_name="McDonald"
        )
        course = CourseBase.create(
            User.objects.create_user("owner"), "CPSC 999 2026W1", True
        )
        self.member = CourseMember.objects.create(
            course=course, user=self.user, role="student", active=True
        )

    def fresh(self):
        """The member as a request would load it.

        Profile.objects.create(user=instance) in the post_save receiver puts
        the new profile straight into the user instance's relation cache, so
        a later queryset .update() is invisible to that copy. A request loads
        both from the database, which is what this reproduces -- and it
        exercises the select_related the export depends on.
        """
        return CourseMember.objects.select_related("user", "user__profile").get(
            pk=self.member.pk
        )

    def test_without_a_preferred_name_the_registrar_name_is_used(self):
        self.assertEqual(self.member.get_user_fullname(), "Jonathan McDonald")

    def test_capitalisation_is_left_alone(self):
        # str.capitalize() rendered this as "Mcdonald". Also "De silva",
        # "Marie-claire". Names are shown as their owner wrote them.
        self.assertEqual(self.member.get_user_fullname(), "Jonathan McDonald")

    def test_a_preferred_name_wins(self):
        Profile.objects.filter(user=self.user).update(
            preferred_first_name="Jo", preferred_last_name="McDonald"
        )

        self.assertEqual(self.fresh().get_user_fullname(), "Jo McDonald")

    def test_a_preferred_first_name_alone_keeps_the_family_name(self):
        Profile.objects.filter(user=self.user).update(preferred_first_name="Jo")

        self.assertEqual(self.fresh().get_user_fullname(), "Jo McDonald")

    def test_the_registrar_name_is_still_reachable(self):
        Profile.objects.filter(user=self.user).update(preferred_first_name="Jo")

        self.assertEqual(self.fresh().get_registrar_fullname(), "Jonathan McDonald")

    def test_an_account_with_no_profile_does_not_crash(self):
        Profile.objects.filter(user=self.user).delete()
        self.member.user.refresh_from_db()

        self.assertEqual(
            CourseMember.objects.get(pk=self.member.pk).get_user_fullname(),
            "Jonathan McDonald",
        )


class ItSurvivesTheNextSignInTests(TestCase):
    """The whole point."""

    def test_a_cwl_sign_in_does_not_undo_a_preferred_name(self):
        user = User.objects.create_user(
            "12345678", first_name="Jonathan", last_name="McDonald"
        )
        Profile.objects.filter(user=user).update(preferred_first_name="Jo")

        # What custom_saml_acs does with the assertion on every sign-in.
        for field, value in (
            ("first_name", "Jonathan"), ("last_name", "McDonald"),
            ("email", "jm@student.ubc.ca"),
        ):
            setattr(user, field, value)
        user.save(update_fields=["first_name", "last_name", "email"])

        self.assertEqual(Profile.objects.get(user=user).preferred_first_name, "Jo")

    def test_the_registrar_name_still_updates_underneath(self):
        # UBC remains the source of truth for the legal name; a preferred
        # name sits beside it rather than replacing it.
        user = User.objects.create_user("12345678", first_name="Jon")
        Profile.objects.filter(user=user).update(preferred_first_name="Jo")

        user.first_name = "Jonathan"
        user.save(update_fields=["first_name"])

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Jonathan")
        self.assertEqual(Profile.objects.get(user=user).preferred_first_name, "Jo")


class WhatAStudentMayEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "12345678", password="x!23456", first_name="Jonathan",
            last_name="McDonald",
        )
        Profile.objects.filter(user=self.user).update(cwl="jmcd")
        self.client = Client()
        self.client.login(username="12345678", password="x!23456")
        self.url = reverse("account:edit", kwargs={"uid": self.user.id})

    def test_the_form_offers_a_preferred_name(self):
        body = self.client.get(self.url).content.decode()

        self.assertIn("preferred_first_name", body)
        self.assertIn("preferred_last_name", body)

    def test_a_student_can_set_one(self):
        self.client.post(self.url, {
            "username": "12345678", "email": "", "first_name": "Jonathan",
            "last_name": "McDonald", "preferred_first_name": "Jo",
            "preferred_last_name": "",
        })

        self.assertEqual(Profile.objects.get(user=self.user).preferred_first_name, "Jo")

    def test_a_student_cannot_change_their_student_number(self):
        self.client.post(self.url, {
            "username": "99999999", "email": "", "first_name": "Jonathan",
            "last_name": "McDonald", "preferred_first_name": "",
            "preferred_last_name": "",
        })

        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "12345678")

    def test_a_student_cannot_change_the_registrar_name(self):
        self.client.post(self.url, {
            "username": "12345678", "email": "", "first_name": "Somebody",
            "last_name": "Else", "preferred_first_name": "",
            "preferred_last_name": "",
        })

        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Jonathan")
        self.assertEqual(self.user.last_name, "McDonald")

    def test_a_student_cannot_change_their_cwl(self):
        # It is not a field on either form; posting it must do nothing.
        self.client.post(self.url, {
            "username": "12345678", "email": "", "first_name": "Jonathan",
            "last_name": "McDonald", "preferred_first_name": "",
            "preferred_last_name": "", "cwl": "someoneelse",
        })

        self.assertEqual(Profile.objects.get(user=self.user).cwl, "jmcd")
