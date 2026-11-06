"""The schedule editor: the weekly pattern and the lectures it produced."""

import re
from datetime import date, time
from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from peer_course.forms import CourseSessionForm, LectureFormSet
from peer_course.models import Course, CourseSession

TEMPLATE = Path(__file__).resolve().parent / "templates" / "course-edit.html"


class ServerSideValidationTests(TestCase):
    """Both orderings lived only in JavaScript, so anything that skipped the
    script could store a session that ends before it starts."""

    def _form(self, **overrides):
        data = {
            "start_time_pst": "10:00",
            "end_time_pst": "11:00",
            "session_date": "2026-01-06",
            "session_end_date": "2026-04-03",
            "active_days": ["mon"],
        }
        data.update(overrides)
        return CourseSessionForm(data)

    def test_a_valid_range_is_accepted(self):
        self.assertTrue(self._form().is_valid())

    def test_an_end_date_before_the_start_is_refused(self):
        form = self._form(session_end_date="2026-01-05")
        self.assertFalse(form.is_valid())
        self.assertIn("session_end_date", form.errors)

    def test_the_same_start_and_end_date_is_allowed(self):
        # A single-day session is legitimate.
        self.assertTrue(self._form(session_end_date="2026-01-06").is_valid())

    def test_a_zero_length_session_is_refused(self):
        form = self._form(end_time_pst="10:00")
        self.assertFalse(form.is_valid())
        self.assertIn("end_time_pst", form.errors)

    def test_a_pattern_with_no_dates_is_still_time_checked(self):
        # Both checks used to be guarded by all four fields being present, so a
        # weekly pattern carrying times but no dates escaped validation.
        form = CourseSessionForm(
            {"start_time_pst": "11:00", "end_time_pst": "10:00",
             "active_days": ["mon"], "is_template": "on"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("end_time_pst", form.errors)

    def test_errors_land_on_their_field_not_on_the_form(self):
        # `__all__` errors were never rendered by the settings page, so the
        # form appeared to save and silently did not.
        form = self._form(session_end_date="2026-01-05")
        form.is_valid()
        self.assertNotIn("__all__", form.errors)

    def test_the_duplicated_browser_handler_is_gone(self):
        # It sat inside the formset loop, so five templates registered five
        # identical listeners and showed five alerts on one bad date.
        source = TEMPLATE.read_text()
        self.assertNotIn('addEventListener("submit"', source)


class SessionNameTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Named", browsable=True, archived=False, agora_mode=True
        )

    def lecture(self, **kwargs):
        return CourseSession.objects.create(
            course=self.course,
            session_date=kwargs.pop("session_date", date(2026, 1, 13)),
            start_time_pst=time(10, 0),
            end_time_pst=time(11, 0),
            is_template=False,
            **kwargs,
        )

    def test_an_unnamed_lecture_shows_its_date(self):
        self.assertEqual(self.lecture().display_name, "13 Jan 2026")

    def test_a_named_lecture_shows_its_name(self):
        self.assertEqual(
            self.lecture(name="Week 3: normalisation").display_name,
            "Week 3: normalisation",
        )

    def test_whitespace_is_not_a_name(self):
        self.assertEqual(self.lecture(name="   ").display_name, "13 Jan 2026")

    def test_a_template_describes_its_pattern(self):
        pattern = CourseSession.objects.create(
            course=self.course,
            start_time_pst=time(10, 0),
            end_time_pst=time(11, 0),
            is_template=True,
            active_days="mon,wed",
        )
        self.assertEqual(pattern.display_name, "Weekly: mon, wed")


class LectureEditingTests(TestCase):
    """Individual lectures were previously uneditable: the settings page only
    ever showed templates, so a name or an exclusion had nowhere to live."""

    def setUp(self):
        self.course = Course.objects.create(
            displayname="Lectures", browsable=True, archived=False, agora_mode=True
        )
        self.first = CourseSession.objects.create(
            course=self.course, session_date=date(2026, 1, 6),
            start_time_pst=time(10, 0), end_time_pst=time(11, 0), is_template=False,
        )
        self.second = CourseSession.objects.create(
            course=self.course, session_date=date(2026, 1, 8),
            start_time_pst=time(10, 0), end_time_pst=time(11, 0), is_template=False,
        )

    def _formset_data(self, **overrides):
        data = {
            "lectures-TOTAL_FORMS": "2",
            "lectures-INITIAL_FORMS": "2",
            "lectures-MIN_NUM_FORMS": "0",
            "lectures-MAX_NUM_FORMS": "1000",
            "lectures-0-id": str(self.first.pk),
            "lectures-0-name": "",
            "lectures-0-counts_for_participation": "on",
            "lectures-1-id": str(self.second.pk),
            "lectures-1-name": "",
            "lectures-1-counts_for_participation": "on",
        }
        data.update(overrides)
        return data

    def queryset(self):
        return self.course.sessions.filter(is_template=False).order_by("session_date")

    def test_a_lecture_can_be_named(self):
        formset = LectureFormSet(
            self._formset_data(**{"lectures-0-name": "Introduction"}),
            queryset=self.queryset(), prefix="lectures",
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        self.first.refresh_from_db()
        self.assertEqual(self.first.name, "Introduction")

    def test_a_lecture_can_be_excluded_from_participation(self):
        data = self._formset_data()
        del data["lectures-0-counts_for_participation"]  # unchecked
        formset = LectureFormSet(
            data, queryset=self.queryset(), prefix="lectures"
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertFalse(self.first.counts_for_participation)
        self.assertTrue(self.second.counts_for_participation)

    def test_editing_lectures_does_not_touch_the_weekly_pattern(self):
        pattern = CourseSession.objects.create(
            course=self.course, start_time_pst=time(9, 0), end_time_pst=time(10, 0),
            is_template=True, active_days="mon",
        )
        formset = LectureFormSet(
            self._formset_data(**{"lectures-0-name": "Renamed"}),
            queryset=self.queryset(), prefix="lectures",
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        pattern.refresh_from_db()
        self.assertTrue(pattern.is_template)
        self.assertEqual(pattern.name, "")


class TemplateStructureTests(TestCase):
    def test_the_two_are_presented_as_different_things(self):
        source = TEMPLATE.read_text()
        self.assertIn('id="weekly-schedule"', source)
        self.assertIn('id="lectures"', source)

    def test_the_lectures_table_has_headers_and_labels(self):
        source = TEMPLATE.read_text()
        table = re.search(r'<table[^>]*id="lectures-table".*?</table>', source, re.S)
        self.assertIsNotNone(table, "the lectures table moved; retarget this guard")
        body = table.group(0)
        self.assertIn("<caption", body)
        # Date, name, counts toward the grade, no lecture.
        self.assertEqual(body.count('scope="col"'), 4)
        self.assertIn('scope="row"', body)
        # Every input is labelled, and each label points at its own field
        # rather than at a shared id -- the failure that makes a screen reader
        # announce every row identically.
        for field in ("name", "counts_for_participation", "cancelled"):
            with self.subTest(field=field):
                self.assertIn(
                    f'for="{{{{ lecture.{field}.id_for_label }}}}"', body
                )


class LectureListPlacementTests(SimpleTestCase):
    """The lecture list belongs to the course, not to each schedule entry."""

    class _Stub:
        """Stands in for a bound session form; missing attributes render empty."""

    def _render(self, sessions):
        from peer_course.forms import CourseForm
        from peer_course.models import Course

        course = Course(id=315, displayname="CPSC 430 2026W1")
        return render_to_string(
            "course-edit.html",
            {
                "course": course,
                "formCourse": CourseForm(instance=course),
                "session_formset": [self._Stub() for _ in range(sessions)],
                "lecture_formset": [],
                "lists": [],
            },
        )

    def test_it_is_drawn_once_however_many_schedule_entries_exist(self):
        # It used to sit inside the per-session loop, so a course with two
        # schedule entries drew the whole lecture list twice -- and put two
        # elements on id="lectures". One entry hid it, which is why nobody saw.
        for sessions in (0, 1, 2, 3):
            with self.subTest(schedule_entries=sessions):
                page = self._render(sessions)
                drawn = page.count('id="lectures"')
                self.assertEqual(
                    drawn,
                    1,
                    f"lecture list drawn {drawn} times for {sessions} schedule entries",
                )
