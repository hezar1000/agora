"""Settings that say what they do, and do what they say.

Written after an audit turned up four that did not: a time zone that would
have put half of AI 100's term an hour out, two switches named after what they
turn off, a switch that controls nothing at all, and a queue setting the
server never read.
"""

from datetime import datetime
from pathlib import Path

import pytz
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from peer_course.forms import CourseForm
from peer_course.models import Course, CourseMember, HandRaise
from peer_course.templatetags.queue_names import queue_name

TEMPLATE = Path(__file__).resolve().parent / "templates" / "course-edit.html"


class TimeZoneTests(SimpleTestCase):
    def test_the_default_is_the_one_this_province_actually_keeps(self):
        self.assertEqual(
            Course._meta.get_field("course_timezone").get_default(),
            "America/Vancouver",
        )

    def test_us_pacific_would_have_been_an_hour_out_for_half_the_term(self):
        # British Columbia stopped changing its clocks on 8 March 2026;
        # US/Pacific still turns back on 1 November. Four AI 100 lectures fall
        # after that date.
        vancouver = pytz.timezone("America/Vancouver")
        us_pacific = pytz.timezone("US/Pacific")
        for month, day in ((11, 4), (11, 18), (11, 25), (12, 2)):
            with self.subTest(date=f"2026-{month:02d}-{day:02d}"):
                naive = datetime(2026, month, day, 14, 0)
                self.assertNotEqual(
                    vancouver.localize(naive).utcoffset(),
                    us_pacific.localize(naive).utcoffset(),
                )

    def test_the_zones_agree_before_november(self):
        naive = datetime(2026, 9, 9, 14, 0)
        self.assertEqual(
            pytz.timezone("America/Vancouver").localize(naive).utcoffset(),
            pytz.timezone("US/Pacific").localize(naive).utcoffset(),
        )

    def test_canadian_zones_are_offered_first(self):
        groups = Course._meta.get_field("course_timezone").choices
        first_group_name, first_group = groups[0]
        self.assertEqual(first_group_name, "Canada")
        self.assertEqual(first_group[0][0], "America/Vancouver")


class SwitchNamingTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Switches", browsable=True, archived=False,
            agora_mode=True,
        )

    def test_switches_are_named_for_what_they_turn_on(self):
        for name in ("auto_start", "export_prompt_at_end"):
            with self.subTest(field=name):
                field = Course._meta.get_field(name)
                label = str(field.verbose_name).lower()
                for negative in ("prevent", "disable", "cancel", "stop", "no "):
                    self.assertNotIn(
                        negative, label,
                        f"{name} is named after what it turns off: {label!r}",
                    )

    def test_the_helpful_defaults_are_the_on_ones(self):
        # A course that nobody configures should start its lectures and offer
        # its export, which is what every course was doing anyway.
        self.assertTrue(Course._meta.get_field("auto_start").get_default())
        self.assertTrue(
            Course._meta.get_field("export_prompt_at_end").get_default()
        )
        self.assertTrue(self.course.auto_start)
        self.assertTrue(self.course.export_prompt_at_end)

    def test_a_switch_that_controls_nothing_is_not_offered(self):
        # `on_list` is written when a course is created and read nowhere.
        self.assertNotIn("on_list", CourseForm(instance=self.course).fields)
        self.assertFalse(Course._meta.get_field("on_list").editable)

    def test_visibility_holds_the_switch_that_controls_visibility(self):
        groups = {g["id"]: g for g in CourseForm(instance=self.course).fieldsets()}
        names = [field.name for field in groups["visibility"]["fields"]]

        self.assertIn("browsable", names)

    def test_the_page_has_one_control_for_visibility_not_two(self):
        source = TEMPLATE.read_text()
        # It used to be a checkbox nobody could see plus a submit button at the
        # foot of the page posting to another endpoint.
        self.assertNotIn("Hide from students", source)
        self.assertNotIn("Show to students", source)


class QueueTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Queues", browsable=True, archived=False,
            agora_mode=True,
        )

    def test_a_renamed_queue_is_shown_by_its_name_in_the_settings(self):
        self.course.color_config = (
            '{"green": "Questions", "blue": "Answers", '
            '"red": "Red", "yellow": "Yellow"}'
        )
        self.course.save()

        labels = self.course.queue_labels()

        self.assertEqual(queue_name(labels, "1"), "Questions")
        self.assertEqual(queue_name(labels, 2), "Answers")

    def test_an_unnamed_queue_still_reads_as_something(self):
        self.assertEqual(queue_name({}, "3"), "Queue 3")
        self.assertEqual(queue_name(None, "3"), "Queue 3")

    def test_the_settings_page_asks_for_the_queue_name(self):
        source = TEMPLATE.read_text()
        self.assertIn("queue_name:choice.data.value", source)
        # choice_value is not an attribute of a bound checkbox; asking for it
        # raised on every render of the settings page.
        self.assertNotIn("choice.choice_value", source)


class SwitchingQueuesTests(TestCase):
    """A student who clicks another queue means that one."""

    def setUp(self):
        self.course = Course.objects.create(
            displayname="Switching", browsable=True, archived=False,
            agora_mode=True, allow_multiple_queues=False,
        )
        self.student = CourseMember.objects.create(
            course=self.course, role="student",
            user=User.objects.create_user("switcher"),
        )

    def test_the_server_used_to_ignore_the_setting_entirely(self):
        source = (
            Path(__file__).resolve().parent.parent
            / "peer_home" / "views.py"
        ).read_text()
        # The refusal was unconditional: a course with multiple queues turned
        # on was refused all the same.
        self.assertIn("course.allow_multiple_queues", source)

    def test_moving_between_queues_closes_the_first_raise(self):
        from peer_course import hand_events

        member = CourseMember.objects.get(pk=self.student.pk)
        member.hand_up = True
        member.save()
        self.assertEqual(
            HandRaise.objects.filter(member=member, resolved_at__isnull=True).count(),
            1,
        )

        member = CourseMember.objects.get(pk=self.student.pk)
        with hand_events.resolving_as(HandRaise.Resolution.MOVED):
            member.hand_up = False
            member.save(update_fields=["hand_up"])
        member = CourseMember.objects.get(pk=self.student.pk)
        member.hand_up_list_2 = True
        member.save(update_fields=["hand_up_list_2"])

        open_raises = HandRaise.objects.filter(
            member=member, resolved_at__isnull=True
        )
        self.assertEqual(open_raises.count(), 1)
        self.assertEqual(open_raises.first().participation_list, 2)
        moved = HandRaise.objects.filter(resolution=HandRaise.Resolution.MOVED)
        self.assertEqual(moved.count(), 1)

    def test_moved_is_not_described_as_only_a_poll_s_doing(self):
        self.assertEqual(
            HandRaise.Resolution.MOVED.label, "Moved to another list"
        )


class RenamedFieldsHaveNoStaleReadersTests(SimpleTestCase):
    """A renamed setting has to be renamed everywhere it is read.

    `auto_start_disabled` and `email_end_lecture_popup` became `auto_start`
    and `export_prompt_at_end`. Three template references were left behind,
    and because Django renders a missing attribute as the empty string they
    failed silently: the End Lecture button lost its export pop-up and the
    page stopped reloading. Two of the three also had to invert, the old names
    being what they turned off.
    """

    RETIRED = ("email_end_lecture_popup", "auto_start_disabled")

    def test_no_template_reads_a_retired_name(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        offenders = {}
        for template in root.glob("peer_*/templates/**/*.html"):
            source = template.read_text()
            for name in self.RETIRED:
                if name in source:
                    offenders.setdefault(str(template.relative_to(root)), []).append(name)
        self.assertEqual(offenders, {})

    def test_no_python_reads_a_retired_name(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        offenders = {}
        for module in root.glob("peer_*/**/*.py"):
            if "migrations" in module.parts or module.name.startswith("test_"):
                continue
            source = module.read_text()
            for name in self.RETIRED:
                if name in source:
                    offenders.setdefault(str(module.relative_to(root)), []).append(name)
        self.assertEqual(offenders, {})
