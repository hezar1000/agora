"""The sign-in page steers everyone to CWL."""

import re
from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase

PAGE = Path(__file__).resolve().parent / "templates" / "user-signup-and-login.html"


class LoginPageTests(SimpleTestCase):
    def setUp(self):
        self.source = PAGE.read_text()

    def test_cwl_is_offered_to_everyone_not_only_students(self):
        # Instructors sign in with CWL too, so the label no longer excludes
        # them by naming students.
        self.assertIn("Log in with CWL", self.source)
        self.assertNotIn("UBC Students: log in with CWL", self.source)

    def test_cwl_is_the_only_thing_presented_as_a_button(self):
        buttons = re.findall(r'class="ui[^"]*button"', self.source)
        for button in buttons:
            with self.subTest(button=button):
                self.assertNotIn("fluid basic", button)

    def test_local_account_routes_are_labelled_as_cwl_fallbacks(self):
        self.assertIn("Not a UBC student, or is CWL not working?", self.source)
        self.assertIn('class="local-login-toggle"', self.source)
        self.assertIn("Sign in with an Agora account", self.source)
        self.assertIn("Create an Agora account", self.source)

    def test_the_quiet_link_sits_outside_the_white_card(self):
        # This is the bug that shipped: the link is coloured for the dark page
        # background, but it was rendered inside the white segment. White text
        # on a white card -- present, clickable, and completely unreadable, with
        # :hover making it worse by going to pure white. Every other assertion
        # in this file passed the whole time, because none of them asked where
        # the thing actually was.
        card = self.source.index('<div class="ui stacked segment">')
        toggle = self.source.index('id="toggle-local-login"')
        self.assertLess(card, toggle)
        between = self.source[card:toggle]
        self.assertEqual(
            between.count("<div"),
            between.count("</div>"),
            "the password link is inside the white card again, where its light "
            "text is invisible",
        )

    def test_the_local_form_can_be_submitted_without_a_mouse(self):
        # A <div> takes no keyboard events, and a form with two fields and no
        # submit button gets no implicit submission either, so Enter did
        # nothing. This is the only door for anyone without a CWL.
        self.assertNotIn("submit button\">Login with", self.source)
        self.assertRegex(self.source, r'<button type="submit"[^>]*>Login with \w+ Account</button>')

    def test_signup_rules_are_not_applied_to_the_login_form(self):
        # The rules include a six-character minimum on `password`, and the login
        # form has a field of that name: applying them there locks out any
        # legacy account with a shorter password.
        self.assertIn("$('#signup-form').form({", self.source)
        self.assertNotIn("$('.ui.form').form({", self.source)

    def test_it_is_still_a_real_button_for_keyboard_and_screen_readers(self):
        # Styled as a link, but a link that goes nowhere is not a link.
        toggle = re.search(r"<button id=\"toggle-local-login\".*?>", self.source, re.S)
        self.assertIsNotNone(toggle)
        self.assertIn('type="button"', toggle.group(0))
        self.assertIn('aria-expanded="false"', toggle.group(0))
        self.assertIn('aria-controls="local-login-container"', toggle.group(0))

    def test_focus_stays_visible_on_the_small_target(self):
        # It is the only way in for anyone without a CWL.
        self.assertIn(".local-login-toggle:focus-visible", self.source)

    def test_the_announced_state_is_read_after_the_animation(self):
        # slideToggle leaves the panel visible for the whole of a slide-up, so
        # reading `:visible` immediately reports the opposite of what happened.
        self.assertIn("$panel.slideToggle(function () {", self.source)
        self.assertIn(
            "$toggle.attr('aria-expanded', $panel.is(':visible') ? 'true' : 'false');",
            self.source,
        )

    def test_a_failed_password_attempt_leaves_the_form_open_and_announced(self):
        self.assertIn("$('#toggle-local-login').attr('aria-expanded', 'true');", self.source)

    def test_the_login_page_does_not_link_to_itself(self):
        # With signup disabled the footer fell through to its else branch and
        # rendered "Already have an account? (Not UBC) Log in" -- on the login
        # page, linking to the login page. A visible control that does nothing,
        # sitting next to the real one that was invisible.
        page = render_to_string(
            "user-signup-and-login.html",
            {
                "is_login": True,
                "SIGNUP_ENABLED": False,
                "GUEST_SESSIONS_ENABLED": False,
                "SAML2_INTEGRATION_ENABLED": True,
            },
        )
        self.assertIn("Log in with CWL", page)
        self.assertIn("Sign in with an Agora account", page)
        self.assertNotIn("Already have an account", page)

    def test_demo_doors_follow_the_guest_sessions_switch(self):
        def page(enabled):
            return render_to_string(
                "user-signup-and-login.html",
                {
                    "is_login": True,
                    "SIGNUP_ENABLED": True,
                    "GUEST_SESSIONS_ENABLED": enabled,
                    "SAML2_INTEGRATION_ENABLED": True,
                },
            )
        self.assertIn("Join a lecture or demo class with a code", page(True))
        self.assertIn("Start a demo class as the instructor", page(True))
        self.assertNotIn("demo class with a code", page(False))
        self.assertNotIn("Start a demo class", page(False))

    def test_signup_and_signin_links_preserve_the_destination(self):
        login_page = render_to_string(
            "user-signup-and-login.html",
            {"is_login": True, "SIGNUP_ENABLED": True, "next": "/course/list/"},
        )
        signup_page = render_to_string(
            "user-signup-and-login.html",
            {"is_login": False, "next": "/course/list/"},
        )
        self.assertIn("/account/signup/?next=/course/list/", login_page)
        self.assertIn("/account/login/?next=/course/list/", signup_page)
