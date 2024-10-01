from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class TestBasicAuth(TestCase):
    def test_login_required(self):
        response = self.client.get(reverse("home:home"), follow=True)
        self.assertEquals(response.context["user"].is_authenticated, False)
        self.assertTrue("login" in response.redirect_chain[0][0])

    def test_login(self):
        password = "12345"
        user = User._default_manager.create_user(username="testuser", password=password)

        self.client.login(username=user.username, password=password)  # login
        response = self.client.get(reverse("home:home"), follow=True)

        self.assertEquals(response.context["user"].is_authenticated, True)
        self.assertEquals(response.context["user"].username, user.username)


class TestCourseSelect(TestCase):
    fixtures = ["initial_course_data"]

    def setUp(self):
        self.client.login(username="alicegao", password="alicealice")  # login

    def test_logged_in(self):
        response = self.client.get(reverse("home:home"), follow=True)
        self.assertEquals(response.context["user"].is_authenticated, True)

    def test_chosen_course_required(self):
        response = self.client.get(reverse("home:home"), follow=True)
        self.assertTrue(reverse("course:list") in response.redirect_chain[0][0])


class TestHomepage(TestCase):
    fixtures = ["initial_course_data"]

    def setUp(self):
        self.client.login(username="alicegao", password="alicealice")  # login
        self.client.get(
            reverse("course:view", kwargs={"cid": 1})
        )  # choose course: CPSC 430

    def test_course_chosen(self):
        response = self.client.get(reverse("home:home"), follow=True)
        self.assertTrue(
            not response.redirect_chain  # either no redirection
            or reverse(  # or first redirection is not to course:list to choose a course
                "course:list"
            )
            not in response.redirect_chain[0][0]
        )
