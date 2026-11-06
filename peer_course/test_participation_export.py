"""What the participation reports contain, and what they must never contain."""

import csv
import io
from datetime import date, time, timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from peer_course import participation_export as export
from peer_course import participation_presets as presets
from peer_course.participation_testing import record_contributions
from peer_course.models import (
    Course, CourseMember, CourseSession, HandRaise,
)


class ReportTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Export", browsable=False, archived=False,
            course_timezone="US/Pacific",
        )
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.scheme = self.course.participation_scheme
        self.scheme.lectures_expected = 4
        self.scheme.dropped_lectures = 1
        self.scheme.save()

        self.lectures = [
            CourseSession.objects.create(
                course=self.course, is_template=False, name=f"Lecture {n + 1}",
                session_date=date(2026, 9, 9) + timedelta(days=7 * n),
                start_time_pst=time(14, 0), end_time_pst=time(15, 30),
                counts_for_participation=(n > 0),
            )
            for n in range(4)
        ]
        self.cancelled = CourseSession.objects.create(
            course=self.course, is_template=False, name="Holiday",
            session_date=date(2026, 10, 14),
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
            cancelled=True, counts_for_participation=False,
        )
        self.pattern = CourseSession.objects.create(
            course=self.course, is_template=True, active_days="wed",
            start_time_pst=time(14, 0), end_time_pst=time(15, 30),
        )
        self.student = self.enrol("keen", "Keen", "Student")

    def enrol(self, username, first, last):
        user = User.objects.create_user(
            username=username, first_name=first, last_name=last,
            email=f"{username}@example.edu",
        )
        return CourseMember.objects.create(
            course=self.course, user=user, role="student"
        )

    def raise_hand(self, member, session, times=1, spoke=0):
        record_contributions(member, session, times, spoke=spoke)

    def rows(self, kind, **kwargs):
        return export.build(self.course, kind=kind, **kwargs)

    def header_of(self, rows):
        return next(row for row in rows if row and row[0] == "Student name")

    def row_for(self, rows, name):
        header = self.header_of(rows)
        index = rows.index(header)
        return next(row for row in rows[index + 1:] if row and row[0] == name)

    # -- what goes in the gradebook ---------------------------------------

    def test_the_grades_report_carries_the_marks(self):
        for lecture in self.lectures[1:]:
            self.raise_hand(self.student, lecture, times=3)

        rows = self.rows(export.GRADES)
        header = self.header_of(rows)
        row = self.row_for(rows, "Keen Student")

        self.assertIn("Lectures attended", header)
        self.assertIn("Participation (of 15)", header)
        self.assertIn("Participation %", header)
        # Three lectures attended of the three graded, nine raises less the
        # three that paid for attendance.
        self.assertEqual(row[header.index("Lectures attended")], 3)
        self.assertEqual(row[header.index("Contributions counted")], 6)

    def test_the_grades_report_is_the_default(self):
        for value in ("", "points_only", "nonsense", None):
            with self.subTest(value=value):
                self.assertEqual(export.resolve_kind(value), export.GRADES)

    def test_old_links_still_resolve(self):
        self.assertEqual(export.resolve_kind("all"), export.FULL)
        self.assertEqual(export.resolve_kind("points_only"), export.GRADES)

    # -- how a grade was reached ------------------------------------------

    def test_the_lecture_report_has_one_column_per_lecture(self):
        self.raise_hand(self.student, self.lectures[0])
        self.raise_hand(self.student, self.lectures[1], times=2)
        self.raise_hand(self.student, self.lectures[2])

        rows = self.rows(export.LECTURES)
        header = self.header_of(rows)
        row = self.row_for(rows, "Keen Student")

        lecture_columns = [cell for cell in header if cell.startswith("2026-")]
        self.assertEqual(len(lecture_columns), 3)
        self.assertEqual(row[header.index(lecture_columns[1])], 2)

    def test_a_lecture_in_which_nothing_happened_is_not_a_column(self):
        # Lecture 4 and the holiday: nobody raised a hand, nobody was called,
        # no roll call. Not real lectures, so not lectures anybody missed.
        self.raise_hand(self.student, self.lectures[0])
        header = self.header_of(self.rows(export.LECTURES))
        labels = [cell for cell in header if cell.startswith("2026-")]
        self.assertEqual(len(labels), 1)
        self.assertIn("Lecture 1", labels[0])

    def test_a_lecture_that_does_not_count_says_so_in_its_column(self):
        # Hands were raised in both before they were excluded.
        self.raise_hand(self.student, self.lectures[0])
        self.raise_hand(self.student, self.cancelled)
        header = self.header_of(self.rows(export.LECTURES))
        labels = [cell for cell in header if cell.startswith("2026-")]

        self.assertIn("(not graded)", labels[0])
        self.assertTrue(any("(no lecture)" in label for label in labels))

    def test_a_weekly_pattern_is_never_a_column(self):
        for kind in (export.GRADES, export.LECTURES, export.FULL):
            with self.subTest(kind=kind):
                header = self.header_of(self.rows(kind))
                self.assertFalse(
                    any("no date" in cell for cell in header),
                    "a weekly pattern reached the spreadsheet",
                )

    def test_an_excluded_lecture_is_left_out_of_the_totals(self):
        self.raise_hand(self.student, self.lectures[0], times=9)

        rows = self.rows(export.LECTURES)
        header = self.header_of(rows)
        row = self.row_for(rows, "Keen Student")

        self.assertEqual(row[header.index("Lectures attended")], 0)
        self.assertEqual(row[header.index("Total hand raises")], 0)

    # -- the file explains itself -----------------------------------------

    def test_every_report_names_the_course_and_the_rubric(self):
        for kind in (export.GRADES, export.LECTURES, export.FULL):
            with self.subTest(kind=kind):
                rows = self.rows(kind)
                preamble = " ".join(cell for row in rows[:14] for cell in row)
                self.assertIn("Export", preamble)
                self.assertIn("graded out of 15 marks", preamble)
                self.assertIn("Attendance", preamble)

    def test_a_course_with_no_scheme_still_exports(self):
        bare = Course.objects.create(
            displayname="Bare", browsable=False, archived=False, 
        )
        rows = export.build(bare, kind=export.GRADES)
        self.assertTrue(any("no participation scheme" in " ".join(row)
                            for row in rows if row))

    # -- it must not get slower with the class ----------------------------

    def test_the_report_does_not_query_once_per_student(self):
        for index in range(40):
            student = self.enrol(f"s{index}", "Many", f"Student{index}")
            for lecture in self.lectures[1:]:
                self.raise_hand(student, lecture, times=2)

        with CaptureQueriesContext(connection) as queries:
            self.rows(export.GRADES)

        self.assertLess(
            len(queries), 20,
            f"{len(queries)} queries for 41 students; the export is walking rows",
        )

    def test_the_csv_round_trips(self):
        rows = self.rows(export.GRADES)
        buffer = io.StringIO()
        csv.writer(buffer).writerows(rows)
        parsed = list(csv.reader(io.StringIO(buffer.getvalue())))
        self.assertEqual(parsed[0][0], rows[0][0])
