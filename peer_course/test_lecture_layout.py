"""The lecture screen on a screen that is not a laptop.

An instructor runs a lecture from whatever is on the lectern, and on a
tablet this page used to hand them a 209px column with the queue's colour
cut off the right-hand edge, a course title hidden behind the navigation
bar, and no way to pinch and look closer. Separately -- and on every device
-- the queue stopped redrawing after the first refresh.

The three-panel layout itself is CSS, which Django cannot execute, so these
read the templates for the specific mistakes that were made. Measured
behaviour behind them: at 1024x768 the queue showed 2.1 rows of 12 before
and 5.3 after, and no page scrolls sideways at 390, 768, 1024 or 1440.
"""

from datetime import date, time
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from peer_course.models import Course, CourseMember, CourseSession
from peer_lecture.models import Lecture

TEMPLATES = Path(__file__).resolve().parent.parent
LECTURE_PAGE = TEMPLATES / "peer_course" / "templates" / "agora.html"
BASE_PAGE = TEMPLATES / "peer_home" / "templates" / "base.html"


class QueueRedrawTests(SimpleTestCase):
    """Chart.js 4 refuses to reuse a canvas, and threw where it was fatal."""

    def setUp(self):
        self.page = LECTURE_PAGE.read_text()

    def test_the_old_chart_is_destroyed_before_a_new_one_is_made(self):
        # Without this, the second call throws "Canvas is already in use" and
        # nothing after it in the success handler runs.
        self.assertIn('Chart.getChart("myChart")', self.page)
        self.assertIn("existing.destroy()", self.page)

    def test_the_queue_is_written_before_the_chart_is_drawn(self):
        # The order is the whole bug: plot_chart used to run first, so its
        # throw meant the list of raised hands was never updated.
        body = self.page
        table = body.index("$('#table').html(rows)")
        chart = body.index("plot_chart(data)", body.index("let rows = create_table(data)"))
        self.assertLess(table, chart)


class LayoutTests(SimpleTestCase):
    def setUp(self):
        self.page = LECTURE_PAGE.read_text()

    def test_the_panels_go_three_across_from_a_tablet_up(self):
        # col-md-4 only applies from 992px, so a 768pt tablet stacked them.
        self.assertNotIn('class="divider col-md-4"', self.page)
        self.assertIn("col-xs-12 col-sm-4 lecture-panel", self.page)

    def test_the_panels_no_longer_have_a_fixed_height(self):
        # 500px boxes with a 170px window onto the queue inside.
        self.assertNotIn('style="height: 500px;', self.page)
        self.assertIn(".lecture-panel { min-height: 500px; }", self.page)

    def test_the_queue_window_grows_with_the_window(self):
        self.assertIn(".lecture-scroll--hands", self.page)
        self.assertIn("calc(100vh - 520px)", self.page)
        # And the old fixed height survives as the floor.
        self.assertIn(".lecture-scroll--hands  { height: 170px; }", self.page)

    def test_a_short_screen_puts_the_queue_before_the_picture_of_it(self):
        # On a landscape tablet the first name sat 729px down a 768px screen,
        # behind five rows of buttons and a bar chart of numbers that are
        # already written out in words directly above it.
        self.assertIn(".lecture-panel--hands .lecture-scroll--hands { order: 2; }",
                      self.page)
        self.assertIn(".lecture-panel--hands .lecture-chart { order: 3; }",
                      self.page)
        self.assertIn("#control-buttons .btn { padding: 7px 12px", self.page)

    def test_the_queue_table_is_laid_out_to_fit_its_column(self):
        # #table is the tbody; a table-layout rule on it does nothing.
        self.assertIn(".lecture-scroll table { table-layout: fixed", self.page)


class NavigationBarOffsetTests(SimpleTestCase):
    def test_the_page_starts_below_the_bar_it_actually_has(self):
        base = BASE_PAGE.read_text()

        # The bar is fixed and 70px was hardcoded beneath it. At 768pt it
        # wraps to two rows, stands 118px tall, and covered the course title.
        self.assertIn("nav.offsetHeight", base)
        self.assertIn("orientationchange", base)
        # The constant stays as the no-JavaScript floor.
        self.assertIn("padding-top: 70px;", base)


class PinchToZoomTests(SimpleTestCase):
    """A table wider than a phone column is read by zooming in."""

    def test_no_page_forbids_zooming(self):
        offenders = []
        for path in sorted(TEMPLATES.glob("peer_*/templates/**/*.html")):
            text = path.read_text(errors="replace")
            if "user-scalable=no" in text or "maximum-scale" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_the_viewport_is_declared_with_commas(self):
        # "width=device-width; initial-scale=1.0" is not the separator the
        # attribute is parsed with.
        offenders = []
        for path in sorted(TEMPLATES.glob("peer_*/templates/**/*.html")):
            for line in path.read_text(errors="replace").splitlines():
                if 'name="viewport"' in line and ";" in line:
                    offenders.append(path.name)
        self.assertEqual(offenders, [])


class ConsoleWidthTests(TestCase):
    """Who gets the whole window and who gets a reading column."""

    def setUp(self):
        self.course = Course.objects.create(
            displayname="CPSC 999 2026W1", browsable=True, archived=False,
        )
        now = timezone.localtime(timezone.now())
        CourseSession.objects.create(
            course=self.course, is_template=False, session_date=now.date(),
            start_time_pst=time(0, 1), end_time_pst=time(23, 59),
        )
        Lecture.objects.create(course=self.course)

    def _page_as(self, role):
        user = User.objects.create_user(f"{role}-user")
        CourseMember.objects.create(
            course=self.course, user=user, role=role, active=True
        )
        client = Client()
        client.force_login(user)
        session = client.session
        session["course_id"] = self.course.id
        session.save()
        return client.get("/agora_dash/").content.decode()

    def test_the_instructor_console_gets_the_whole_window(self):
        # Inside col-md-8 with a two-column offset, a 1024pt tablet left each
        # of the three panels 209px -- narrower than the table inside it.
        body = self._page_as("instructor")

        self.assertNotIn("col-md-8 col-lg-8 col-md-offset-2", body)

    def test_a_ta_running_the_lecture_gets_it_too(self):
        self.assertNotIn("col-md-8 col-lg-8 col-md-offset-2", self._page_as("ta"))

    def test_a_student_keeps_the_reading_width_column(self):
        # Nothing on the student's page is three panels wide.
        self.assertIn("col-md-8 col-lg-8 col-md-offset-2", self._page_as("student"))

    def test_the_offset_that_was_never_a_class_is_gone(self):
        # col-sm-offset-2.5: Bootstrap has no fractional offsets.
        self.assertNotIn("col-sm-offset-2.5", self._page_as("student"))
