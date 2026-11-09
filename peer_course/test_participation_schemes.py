"""Grading a whole course from recorded hand raises, under each preset."""

from datetime import date, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from peer_course import participation_presets as presets
from peer_course import participation_schemes as schemes
from peer_course.participation_testing import record_contributions
from peer_course.models import (
    Course,
    CourseMember,
    CourseParticipation,
    CourseSession,
    EarlierCurve,
    HandRaise,
    ParticipationScheme,
)
from peer_course import participation_standing as standing


class SchemeGradingTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            displayname="Schemes", browsable=True, archived=False
        )
        self.sessions = [
            CourseSession.objects.create(
                course=self.course,
                session_date=date(2026, 1, 5) + timedelta(days=7 * week),
                start_time_pst=time(10, 0),
                end_time_pst=time(11, 0),
                is_template=False,
            )
            for week in range(4)
        ]

    def student(self, username):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role="student"
        )

    def contribute(self, member, session, times=1, spoke=0):
        """Record ``times`` contributions in ``session``, ``spoke`` of which spoke."""
        record_contributions(member, session, times, spoke=spoke)

    # -- attendance ------------------------------------------------------

    def test_attendance_counts_lectures_not_contributions(self):
        presets.apply_preset(self.course, presets.ATTENDANCE)
        steady = self.student("steady")
        burst = self.student("burst")
        for session in self.sessions:
            self.contribute(steady, session, times=1)
        self.contribute(burst, self.sessions[0], times=40)

        graded = schemes.grade_course(self.course)

        self.assertAlmostEqual(graded[steady.id]["total"], 1.0)
        self.assertAlmostEqual(graded[burst.id]["total"], 0.25)

    def test_a_cap_of_one_makes_volume_equal_coverage(self):
        # Attendance is not a special case; it is what the model degenerates to.
        presets.apply_preset(self.course, presets.ATTENDANCE)
        scheme = self.course.participation_scheme
        student = self.student("capped")
        self.contribute(student, self.sessions[0], times=9)
        self.contribute(student, self.sessions[1], times=4)

        values = schemes.measure_values(self.course, scheme)

        self.assertEqual(values[student.id][ParticipationScheme.VOLUME], 2.0)
        # Two lectures ran; the other two sessions saw no hands at all and so
        # are not lectures this student can be marked absent from.
        self.assertAlmostEqual(
            values[student.id][ParticipationScheme.COVERAGE], 1.0
        )

    # -- volume ----------------------------------------------------------

    def test_volume_adds_contributions_across_lectures(self):
        presets.apply_preset(self.course, presets.VOLUME)
        student = self.student("talker")
        self.contribute(student, self.sessions[0], times=7)
        self.contribute(student, self.sessions[2], times=8)

        scheme = self.course.participation_scheme
        values = schemes.measure_values(self.course, scheme)

        self.assertEqual(values[student.id][ParticipationScheme.VOLUME], 15.0)
        self.assertAlmostEqual(
            schemes.grade_course(self.course)[student.id]["total"], 0.5
        )

    def test_credit_outside_lectures_adds_to_volume_only(self):
        # Quiz 0 style credit: a row with no lecture and only bonus points.
        presets.apply_preset(self.course, presets.VOLUME)
        scheme = self.course.participation_scheme
        student = self.student("quizzer")
        absent = self.student("absent")
        self.contribute(student, self.sessions[0], times=3)
        self.contribute(absent, self.sessions[1], times=1)
        before = schemes.measure_values(self.course, scheme)
        CourseParticipation.objects.create(
            participant=student, session=None, bonus_points=2,
            participation_points_gained=0, time_participated=timezone.now(),
            count_in_calculations=True, real_participation=False,
        )
        # Neither an undone row nor call credit outside a lecture counts.
        CourseParticipation.objects.create(
            participant=absent, session=None, bonus_points=2,
            participation_points_gained=0, time_participated=timezone.now(),
            count_in_calculations=False, real_participation=False,
        )
        CourseParticipation.objects.create(
            participant=absent, session=None, bonus_points=0,
            participation_points_gained=5, time_participated=timezone.now(),
            count_in_calculations=True, real_participation=False,
        )

        after = schemes.measure_values(self.course, scheme)

        self.assertEqual(schemes.course_bonuses(self.course), {student.id: 2})
        self.assertEqual(
            after[student.id][ParticipationScheme.VOLUME],
            before[student.id][ParticipationScheme.VOLUME] + 2,
        )
        self.assertEqual(
            after[student.id][ParticipationScheme.COVERAGE],
            before[student.id][ParticipationScheme.COVERAGE],
        )
        self.assertEqual(after[absent.id], before[absent.id])
        self.assertNotIn(None, schemes.lecture_tallies(self.course))
        self.assertEqual(standing.standing_for(self.course, student)["course_bonus"], 2)

    def test_the_cap_bounds_what_a_lecture_pays(self):
        # The recorder applies the cap as the lecture happens, speaking bonus
        # included, and the grade is what was recorded.
        presets.apply_preset(self.course, presets.VOLUME)
        scheme = self.course.participation_scheme
        scheme.speak_bonus = 1
        scheme.lecture_cap = 2
        scheme.save()
        student = self.student("speaker")
        self.contribute(student, self.sessions[0], times=5, spoke=2)

        values = schemes.measure_values(self.course, scheme)

        self.assertEqual(values[student.id][ParticipationScheme.VOLUME], 2.0)

    # -- coverage and volume together (the AI 100 draft) -------------------

    def test_coverage_and_volume_measure_different_things(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        scheme.lectures_expected = 4
        scheme.dropped_lectures = 1
        # Counting every raise, so that the two measures are being compared
        # and not the attendance deduction, which has a test of its own below.
        scheme.volume_excludes_covered = False
        scheme.save()

        spread = self.student("spread")
        for session in self.sessions:
            self.contribute(spread, session, times=2)
        concentrated = self.student("concentrated")
        self.contribute(concentrated, self.sessions[0], times=8)

        graded = schemes.grade_course(self.course)

        self.assertAlmostEqual(
            graded[spread.id][ParticipationScheme.COVERAGE], 1.0
        )
        self.assertAlmostEqual(
            graded[concentrated.id][ParticipationScheme.COVERAGE], 1 / 3
        )
        # Same eight contributions, so volume cannot separate them.
        self.assertAlmostEqual(
            graded[spread.id][ParticipationScheme.VOLUME],
            graded[concentrated.id][ParticipationScheme.VOLUME],
        )

    def test_dropped_lectures_forgive_absence(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        scheme.lectures_expected = 4
        scheme.dropped_lectures = 1
        scheme.save()
        student = self.student("absent-once")
        for session in self.sessions[:3]:
            self.contribute(student, session, times=1)

        graded = schemes.grade_course(self.course)

        # Three of four, with one forgiven, is full coverage.
        self.assertAlmostEqual(graded[student.id][ParticipationScheme.COVERAGE], 1.0)

    def test_weights_split_the_grade(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        scheme.lectures_expected = 4
        scheme.dropped_lectures = 0
        # As above: this is about how the two weights blend, so every raise
        # counts and the quiet student's volume is small rather than nil.
        scheme.volume_excludes_covered = False
        scheme.save()
        student = self.student("full-coverage-no-volume")
        for session in self.sessions:
            self.contribute(student, session, times=1)
        # Without classmates this student is their own median and maximum, so
        # the class-relative curve would pay full marks and hide the weighting.
        loud = self.student("loud")
        for session in self.sessions:
            self.contribute(loud, session, times=20)

        graded = schemes.grade_course(self.course)

        # Coverage full at weight 5, volume near nothing at weight 10.
        self.assertAlmostEqual(graded[student.id][ParticipationScheme.COVERAGE], 1.0)
        self.assertGreater(graded[student.id]["total"], 1 / 3)
        self.assertLess(graded[student.id]["total"], 0.9)

    # -- the attendance raise, counted once ---------------------------------

    def test_the_deduction_separates_spread_from_concentrated(self):
        """With attendance paid separately, spreading raises out costs volume.

        Eight raises across four lectures spend four of themselves on
        attendance; eight in a single lecture spend one. The student who
        spread them is paid for that in coverage instead, which is the trade
        the rubric intends.
        """
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        spread = self.student("spread")
        for session in self.sessions:
            self.contribute(spread, session, times=2)
        concentrated = self.student("concentrated")
        self.contribute(concentrated, self.sessions[0], times=8)

        values = schemes.measure_values(self.course, scheme)

        self.assertEqual(values[spread.id][ParticipationScheme.VOLUME], 4)
        self.assertEqual(values[concentrated.id][ParticipationScheme.VOLUME], 7)
        self.assertGreater(
            values[spread.id][ParticipationScheme.COVERAGE],
            values[concentrated.id][ParticipationScheme.COVERAGE],
        )


    def test_contributions_can_exclude_the_raise_that_earned_attendance(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        student = self.student("three-a-lecture")
        for session in self.sessions:
            self.contribute(student, session, times=3)

        values = schemes.measure_values(self.course, scheme)

        # Twelve raises across four lectures, less the four that already paid
        # for attendance.
        self.assertEqual(values[student.id][ParticipationScheme.VOLUME], 8)

    def test_the_deduction_is_off_unless_a_scheme_asks_for_it(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        scheme = self.course.participation_scheme
        self.assertFalse(scheme.volume_excludes_covered)
        student = self.student("counted-in-full")
        for session in self.sessions:
            self.contribute(student, session, times=3)

        values = schemes.measure_values(self.course, scheme)

        self.assertEqual(values[student.id][ParticipationScheme.VOLUME], 12)

    def test_the_deduction_leaves_a_speaking_bonus_alone(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        scheme.speak_bonus = 1
        scheme.save()
        student = self.student("spoke-once")
        self.contribute(student, self.sessions[0], times=2, spoke=1)

        values = schemes.measure_values(self.course, scheme)

        # Two raises worth one each, one of which spoke for another one, less
        # the single raise that earned attendance. It was the raise that paid
        # for attendance, not the speaking.
        self.assertAlmostEqual(values[student.id][ParticipationScheme.VOLUME], 2.0)

    def test_the_deduction_never_takes_a_student_below_nothing(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        scheme = self.course.participation_scheme
        student = self.student("one-each")
        for session in self.sessions:
            self.contribute(student, session, times=1)

        values = schemes.measure_values(self.course, scheme)

        # Every raise was an attendance raise, so no contributions are left
        # over -- and attendance itself is untouched by the deduction.
        self.assertEqual(values[student.id][ParticipationScheme.VOLUME], 0)
        self.assertGreater(values[student.id][ParticipationScheme.COVERAGE], 0)

    # -- per lecture (CPSC 430) -------------------------------------------

    def test_a_quiet_week_does_not_depress_a_full_participant(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        star = self.student("star")
        other = self.student("other")
        # A busy lecture and a quiet one; the star leads both.
        self.contribute(star, self.sessions[0], times=10)
        self.contribute(other, self.sessions[0], times=4)
        self.contribute(star, self.sessions[1], times=1)
        self.contribute(other, self.sessions[1], times=1)

        graded = schemes.grade_course(self.course)

        # Top of the class in the busy lecture, tied at the top of the quiet
        # one; neither lecture drags the other down. The two sessions with no
        # hands at all are not lectures, so they are not averaged in.
        self.assertAlmostEqual(
            graded[star.id][ParticipationScheme.PER_LECTURE], 1.0, places=2
        )

    def test_absence_scores_zero_for_that_lecture(self):
        presets.apply_preset(self.course, presets.PER_LECTURE)
        scheme = self.course.participation_scheme
        scheme.lectures_expected = 4
        # About absence, not about drops: with four dropped of four held
        # everyone would be graded on their single best lecture.
        scheme.dropped_lectures = 0
        scheme.save()
        present = self.student("present")
        for session in self.sessions:
            self.contribute(present, session, times=3)
        sometimes = self.student("sometimes")
        self.contribute(sometimes, self.sessions[0], times=3)

        graded = schemes.grade_course(self.course)

        self.assertGreater(
            graded[present.id][ParticipationScheme.PER_LECTURE],
            graded[sometimes.id][ParticipationScheme.PER_LECTURE],
        )

    # -- safety ------------------------------------------------------------

    def test_a_session_with_no_hands_is_not_a_lecture(self):
        # Sessions are scheduled ahead, so a cancelled lecture leaves its row
        # behind. Counting it would mark every student absent from a lecture
        # that never ran.
        presets.apply_preset(self.course, presets.ATTENDANCE)
        scheme = self.course.participation_scheme
        student = self.student("attended-everything-that-happened")
        for session in self.sessions[:2]:
            self.contribute(student, session, times=1)

        values = schemes.measure_values(self.course, scheme)

        self.assertAlmostEqual(
            values[student.id][ParticipationScheme.COVERAGE], 1.0
        )

    def test_lectures_expected_overrides_the_count(self):
        # A course that knows it has sixteen lectures is graded against
        # sixteen, whether or not every one of them ran.
        presets.apply_preset(self.course, presets.ATTENDANCE)
        scheme = self.course.participation_scheme
        scheme.lectures_expected = 4
        scheme.save()
        student = self.student("two-of-four")
        for session in self.sessions[:2]:
            self.contribute(student, session, times=1)

        values = schemes.measure_values(self.course, scheme)

        self.assertAlmostEqual(
            values[student.id][ParticipationScheme.COVERAGE], 0.5
        )

    def test_an_excluded_lecture_counts_for_nobody(self):
        # The first meeting, a guest lecture, or one cancelled after hands were
        # already up. It must not raise a present student's coverage, nor lower
        # an absent one's.
        presets.apply_preset(self.course, presets.ATTENDANCE)
        scheme = self.course.participation_scheme
        attended_both = self.student("both")
        skipped_the_first = self.student("second-only")
        self.contribute(attended_both, self.sessions[0], times=1)
        self.contribute(attended_both, self.sessions[1], times=1)
        self.contribute(skipped_the_first, self.sessions[1], times=1)

        before = schemes.measure_values(self.course, scheme)
        self.assertAlmostEqual(
            before[skipped_the_first.id][ParticipationScheme.COVERAGE], 0.5
        )

        self.sessions[0].counts_for_participation = False
        self.sessions[0].save(update_fields=["counts_for_participation"])
        after = schemes.measure_values(self.course, scheme)

        # One lecture now counts, and both students attended it.
        self.assertAlmostEqual(
            after[skipped_the_first.id][ParticipationScheme.COVERAGE], 1.0
        )
        self.assertAlmostEqual(
            after[attended_both.id][ParticipationScheme.COVERAGE], 1.0
        )
        self.assertEqual(
            after[attended_both.id][ParticipationScheme.VOLUME], 1.0
        )

    def test_excluding_a_lecture_leaves_its_records_intact(self):
        presets.apply_preset(self.course, presets.ATTENDANCE)
        student = self.student("recorded")
        self.contribute(student, self.sessions[0], times=4)
        self.sessions[0].counts_for_participation = False
        self.sessions[0].save(update_fields=["counts_for_participation"])

        # Excluded from grading, but the record is still there to re-include.
        # (Under the attendance cap the recorder wrote one credit for the four.)
        kept = CourseParticipation.objects.filter(session=self.sessions[0])
        self.assertTrue(kept.exists())
        self.assertEqual(
            sum(row.participation_points_gained for row in kept), 1
        )
        self.assertEqual(
            schemes.lecture_scores(self.course, self.course.participation_scheme),
            {},
        )

    def test_a_course_without_a_scheme_is_left_alone(self):
        self.assertIsNone(schemes.grade_course(self.course))

    def test_existing_courses_map_onto_a_preset(self):
        self.course.max_hand_raise_points = 1
        self.assertEqual(presets.suggest_preset(self.course), presets.ATTENDANCE)
        self.course.max_hand_raise_points = None
        self.assertEqual(presets.suggest_preset(self.course), presets.PER_LECTURE)

    def test_every_preset_grades_without_error(self):
        student = self.student("anyone")
        self.contribute(student, self.sessions[0], times=3, spoke=1)
        for name in presets.PRESETS:
            with self.subTest(preset=name):
                presets.apply_preset(self.course, name)
                self.course.refresh_from_db()
                graded = schemes.grade_course(self.course)
                self.assertIn("total", graded[student.id])
                self.assertGreaterEqual(graded[student.id]["total"], 0.0)
                self.assertLessEqual(graded[student.id]["total"], 1.0)

    def test_switching_preset_does_not_leave_the_old_ones_settings(self):
        # The term-wide scheme sets sixteen lectures; the per-lecture one does
        # not mention them. Without a reset the second inherits the first, and
        # a course with two lectures is graded out of sixteen.
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()
        self.assertEqual(self.course.participation_scheme.lectures_expected, 16)

        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()

        scheme = self.course.participation_scheme
        self.assertIsNone(scheme.lectures_expected)
        self.assertEqual(
            scheme.dropped_lectures,
            presets.PRESETS[presets.PER_LECTURE]["scheme"]["dropped_lectures"],
            "the scheme kept a value the new preset did not ask for",
        )

    def test_applying_a_preset_twice_does_not_stack_rubrics(self):
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()
        self.assertEqual(self.course.participation_scheme.rubrics.count(), 2)


class AI100RubricTests(TestCase):
    """The rubric AI 100 publishes, against its own worked example.

    Seventeen lectures are held. The first is not graded, which leaves sixteen,
    of which the three weakest are dropped -- so attendance is five marks out
    of thirteen. Contributions are ten marks: a quarter mark each up to
    twenty-eight, and above the class median a rank curve from seven to ten.
    Contributions do not count the raise that earned each lecture's attendance.
    """

    LECTURES = 17

    def setUp(self):
        self.course = Course.objects.create(
            displayname="AI 100", browsable=True, archived=False
        )
        self.sessions = [
            CourseSession.objects.create(
                course=self.course,
                session_date=date(2026, 9, 8) + timedelta(days=7 * week),
                start_time_pst=time(9, 30),
                end_time_pst=time(11, 0),
                is_template=False,
                # "We will not grade attendance in the first lecture, to allow
                # late registration and time to set up Agora."
                counts_for_participation=week > 0,
            )
            for week in range(self.LECTURES)
        ]
        self.graded = self.sessions[1:]
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.scheme = self.course.participation_scheme

    def student(self, username):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role="student"
        )

    def raise_hand(self, member, session, times=1):
        record_contributions(member, session, times)

    def test_the_preset_is_the_published_rubric(self):
        self.assertEqual(self.scheme.lectures_expected, 16)
        self.assertEqual(self.scheme.dropped_lectures, 3)
        self.assertTrue(self.scheme.volume_excludes_covered)

        rubrics = {rubric.measure: rubric for rubric in self.scheme.rubrics.all()}
        self.assertEqual(rubrics[ParticipationScheme.COVERAGE].weight, 5.0)
        self.assertEqual(rubrics[ParticipationScheme.VOLUME].weight, 10.0)
        self.assertEqual(
            rubrics[ParticipationScheme.VOLUME].absolute_anchors,
            [{"at": 0, "gives": 0.0}, {"at": 28, "gives": 0.7}],
        )
        self.assertEqual(
            rubrics[ParticipationScheme.VOLUME].relative_anchors,
            [{"at": "median", "gives": 0.7}, {"at": "max", "gives": 1.0}],
        )

    def test_the_first_lecture_counts_for_nothing_either_way(self):
        student = self.student("only-the-first")
        self.raise_hand(student, self.sessions[0], times=20)

        graded = schemes.grade_course(self.course)

        # Twenty raises in an ungraded lecture earn nothing, and the lecture is
        # not one anyone can be marked absent from.
        self.assertEqual(graded[student.id][ParticipationScheme.COVERAGE], 0.0)
        self.assertEqual(
            graded[student.id]["raw"][ParticipationScheme.VOLUME], 0.0
        )

    def test_no_hand_raises_earns_no_contribution_marks(self):
        students = [self.student(f"no-raises-{index}") for index in range(3)]

        graded = schemes.grade_course(self.course)

        for student in students:
            self.assertEqual(
                graded[student.id][ParticipationScheme.VOLUME], 0.0
            )

    def test_attendance_is_full_at_thirteen_of_sixteen(self):
        thirteen = self.student("thirteen")
        for session in self.graded[:13]:
            self.raise_hand(thirteen, session)
        every = self.student("every")
        for session in self.graded:
            self.raise_hand(every, session)

        graded = schemes.grade_course(self.course)

        # Taken literally the rubric divides sixteen attended lectures by
        # thirteen, which is 123% of the attendance marks. Coverage caps.
        self.assertAlmostEqual(graded[thirteen.id][ParticipationScheme.COVERAGE], 1.0)
        self.assertAlmostEqual(graded[every.id][ParticipationScheme.COVERAGE], 1.0)

    def test_missing_four_lectures_costs_attendance_marks(self):
        student = self.student("missed-four")
        for session in self.graded[:12]:
            self.raise_hand(student, session)

        graded = schemes.grade_course(self.course)

        self.assertAlmostEqual(
            graded[student.id][ParticipationScheme.COVERAGE], 12 / 13
        )

    def test_the_worked_example_from_the_rubric(self):
        """"miss two of the 16 lectures and raise your hand twice for each of
        the lectures you do attend (not counting your first attendance raise)"
        is h = 28, which the rubric says earns 7 of 10."""
        student = self.student("worked-example")
        for session in self.graded[:14]:
            self.raise_hand(student, session, times=3)

        values = schemes.measure_values(self.course, self.scheme)

        # Forty-two raises, less the fourteen that paid for attendance.
        self.assertEqual(values[student.id][ParticipationScheme.VOLUME], 28)

    def test_below_the_median_is_paid_by_the_fixed_table(self):
        quiet = self.student("quiet")
        for session in self.graded[:8]:
            self.raise_hand(quiet, session, times=2)
        for index in range(20):
            loud = self.student(f"loud-{index}")
            for session in self.graded:
                self.raise_hand(loud, session, times=5)

        graded = schemes.grade_course(self.course)

        # Sixteen raises less eight attendance raises is h = 8, at a quarter
        # mark each: 2 of the 10 marks contributions are worth.
        self.assertAlmostEqual(
            graded[quiet.id][ParticipationScheme.VOLUME], 0.2, places=3
        )

    def test_the_median_gets_seven_and_the_most_active_gets_ten(self):
        students = []
        for index in range(21):
            student = self.student(f"student-{index}")
            for session in self.graded:
                self.raise_hand(student, session, times=index + 1)
            students.append(student)

        graded = schemes.grade_course(self.course)

        self.assertAlmostEqual(
            graded[students[10].id][ParticipationScheme.VOLUME], 0.7, places=2
        )
        self.assertAlmostEqual(
            graded[students[-1].id][ParticipationScheme.VOLUME], 1.0, places=2
        )

    def test_participation_is_fifteen_marks_split_five_and_ten(self):
        student = self.student("full-attendance-quiet")
        for session in self.graded[:13]:
            self.raise_hand(student, session)
        for index in range(20):
            loud = self.student(f"loud-{index}")
            for session in self.graded:
                self.raise_hand(loud, session, times=6)

        graded = schemes.grade_course(self.course)

        # Attendance full, contributions nothing: 5 of the 15 marks.
        self.assertAlmostEqual(graded[student.id][ParticipationScheme.COVERAGE], 1.0)
        self.assertAlmostEqual(graded[student.id][ParticipationScheme.VOLUME], 0.0)
        self.assertAlmostEqual(graded[student.id]["total"], 5 / 15)


class CPSC430SyllabusTests(TestCase):
    """The per-lecture preset against the rubric CPSC 430 publishes.

    The preset claims to be that course "anchor for anchor", so it is worth
    checking against the syllabus rather than against itself. It was not: the
    syllabus drops two classes and the preset dropped none, so a course seeded
    from it was graded on every class it held.
    """

    def setUp(self):
        self.course = Course.objects.create(
            displayname="CPSC 430 check", browsable=True, archived=False,
        )
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        self.scheme = self.course.participation_scheme

    def student(self, username):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role="student"
        )

    def contribute(self, member, session, times=1):
        record_contributions(member, session, times)

    def test_the_per_class_table_is_the_published_one(self):
        rubric = self.scheme.rubrics.get(measure=ParticipationScheme.PER_LECTURE)

        # 0 -> 0, 1 -> 5, 2 -> 6, 3 -> 6.5, 4 -> 7, median -> 8, out of 10.
        self.assertEqual(
            rubric.absolute_anchors,
            [{"at": 0, "gives": 0.00}, {"at": 1, "gives": 0.50},
             {"at": 2, "gives": 0.60}, {"at": 3, "gives": 0.65},
             {"at": 4, "gives": 0.70}, {"at": "median", "gives": 0.80}],
        )
        self.assertEqual(
            rubric.relative_anchors,
            [{"at": "median", "gives": 0.80}, {"at": "max", "gives": 1.00}],
        )
        self.assertEqual(rubric.combination, "better")

    def test_four_classes_are_dropped(self):
        # "Before averaging, we'll drop the 4 classes in which you have the
        # lowest grades. This means that you can miss 4 classes without any
        # grade penalty."
        self.assertEqual(self.scheme.dropped_lectures, 4)
        self.assertFalse(self.scheme.bonus_after_speaking)

    def test_missing_four_classes_costs_nothing(self):
        sessions = [
            CourseSession.objects.create(
                course=self.course, is_template=False,
                session_date=date(2026, 9, 8) + timedelta(days=2 * n),
                start_time_pst=time(15, 30), end_time_pst=time(17, 0),
            )
            for n in range(8)
        ]
        present = self.student("present-throughout")
        missed_four = self.student("missed-four")
        for session in sessions:
            self.contribute(present, session, times=2)
        for session in sessions[:4]:
            self.contribute(missed_four, session, times=2)

        graded = schemes.grade_course(self.course)

        self.assertAlmostEqual(
            graded[missed_four.id][ParticipationScheme.PER_LECTURE],
            graded[present.id][ParticipationScheme.PER_LECTURE],
        )


class MidTermTableChangeTests(TestCase):
    """CPSC 430 2026W1 changed its per-class table from November on.

    0 hands -> 0, 1 -> 5, 2 -> 6, 3 -> 7, 4 -> 8, 5 -> 9, 6 or more -> 10.
    The lectures already held keep the table they were held under.
    """

    NEW_TABLE = [
        {"at": 0, "gives": 0.0}, {"at": 1, "gives": 0.5},
        {"at": 2, "gives": 0.6}, {"at": 3, "gives": 0.7},
        {"at": 4, "gives": 0.8}, {"at": 5, "gives": 0.9},
        {"at": 6, "gives": 1.0},
    ]

    def setUp(self):
        self.course = Course.objects.create(
            displayname="CPSC 430 change", browsable=True, archived=False,
        )
        presets.apply_preset(self.course, presets.PER_LECTURE)
        self.course.refresh_from_db()
        self.scheme = self.course.participation_scheme
        self.scheme.dropped_lectures = 0
        self.scheme.save()
        self.rubric = self.scheme.rubrics.get(
            measure=ParticipationScheme.PER_LECTURE
        )
        self.before, self.after = (
            CourseSession.objects.create(
                course=self.course, is_template=False, session_date=day,
                start_time_pst=time(15, 30), end_time_pst=time(17, 0),
            )
            for day in (date(2026, 10, 29), date(2026, 11, 3))
        )

    def student(self, username):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role="student"
        )

    def change_table(self, until):
        rubric = self.rubric
        EarlierCurve.objects.create(
            rubric=rubric,
            until=until,
            absolute_anchors=rubric.absolute_anchors,
            absolute_interpolation=rubric.absolute_interpolation,
            relative_anchors=rubric.relative_anchors,
            relative_interpolation=rubric.relative_interpolation,
            combination=rubric.combination,
        )
        rubric.absolute_anchors = self.NEW_TABLE
        rubric.absolute_interpolation = "step"
        rubric.relative_anchors = []
        rubric.combination = "absolute"
        rubric.save()

    def test_the_new_table(self):
        self.change_table(date(2026, 10, 30))
        for hands, gives in [
            (0, 0.0), (1, 0.5), (2, 0.6), (3, 0.7), (4, 0.8), (5, 0.9),
            (6, 1.0), (14, 1.0),
        ]:
            self.assertAlmostEqual(
                schemes.apply_rubric(self.rubric, hands, [hands, 20]), gives
            )

    def test_lectures_before_the_change_keep_the_old_table(self):
        quiet = self.student("quiet")
        for index in range(2):
            loud = self.student(f"loud-{index}")
            for session in (self.before, self.after):
                record_contributions(loud, session, 10)
        for session in (self.before, self.after):
            record_contributions(quiet, session, 4)
        self.change_table(date(2026, 10, 30))

        graded = schemes.grade_course(self.course)
        page = standing.standing_for(self.course, quiet)

        # Four hands: 7 of 10 under the old table, 8 of 10 under the new.
        self.assertEqual(
            [entry["grade"] for entry in page["lecture_grades"]], [0.7, 0.8]
        )
        self.assertAlmostEqual(
            graded[quiet.id][ParticipationScheme.PER_LECTURE], 0.75
        )

    def test_without_an_earlier_curve_every_lecture_uses_the_rubric(self):
        quiet = self.student("quiet")
        for session in (self.before, self.after):
            record_contributions(quiet, session, 4)
        self.change_table(date(2026, 10, 1))

        graded = schemes.grade_course(self.course)

        self.assertAlmostEqual(
            graded[quiet.id][ParticipationScheme.PER_LECTURE], 0.8
        )


class ContributionsAreCallsTests(TestCase):
    """What a contribution is: a hand up when the instructor called on someone.

    Both syllabi say so, the lecture screen has always counted it that way,
    and it is the only definition that cannot be gamed from a seat. Until
    2026-09-16 the grader counted ``HandRaise`` rows -- every time a hand went
    up -- and the first week of term had students raising and lowering a hand
    twenty-seven times in eleven seconds.
    """

    def setUp(self):
        self.course = Course.objects.create(
            displayname="Calls", browsable=True, archived=False
        )
        presets.apply_preset(self.course, presets.COVERAGE_AND_VOLUME)
        self.course.refresh_from_db()
        self.scheme = self.course.participation_scheme
        self.scheme.volume_excludes_covered = False
        # Graded against the lectures held, so one lecture is the whole term.
        self.scheme.lectures_expected = None
        self.scheme.dropped_lectures = 0
        self.scheme.save()
        self.session = CourseSession.objects.create(
            course=self.course, session_date=date(2026, 9, 15),
            start_time_pst=time(15, 30), end_time_pst=time(17, 0),
            is_template=False,
        )

    def student(self, username):
        user = User.objects.create_user(username=username)
        return CourseMember.objects.create(
            course=self.course, user=user, role="student"
        )

    def toggle(self, member, times):
        now = timezone.now()
        for index in range(times):
            HandRaise.objects.create(
                member=member, session=self.session, participation_list=1,
                raised_at=now + timedelta(seconds=index),
                resolved_at=now + timedelta(seconds=index),
                resolution=HandRaise.Resolution.LOWERED,
            )

    def test_toggling_a_hand_between_calls_earns_nothing(self):
        toggler = self.student("toggler")
        self.toggle(toggler, 27)

        values = schemes.measure_values(self.course, self.scheme)

        self.assertEqual(values[toggler.id][ParticipationScheme.VOLUME], 0)
        self.assertEqual(values[toggler.id][ParticipationScheme.COVERAGE], 0)

    def test_a_hand_up_through_three_calls_is_three_contributions(self):
        steady = self.student("steady")
        self.toggle(steady, 1)
        # What the recorder wrote: one row, incremented at each of three calls.
        record_contributions(steady, self.session, 3)

        values = schemes.measure_values(self.course, self.scheme)

        self.assertEqual(values[steady.id][ParticipationScheme.VOLUME], 3)
        self.assertEqual(values[steady.id][ParticipationScheme.COVERAGE], 1.0)

    def test_being_chosen_counts_as_a_contribution(self):
        chosen = self.student("chosen")
        record_contributions(chosen, self.session, 1, spoke=1)

        values = schemes.measure_values(self.course, self.scheme)

        self.assertEqual(values[chosen.id][ParticipationScheme.VOLUME], 1)

    def test_credit_paid_after_speaking_counts(self):
        # CPSC 430: "you'll automatically get participation points as though
        # you raised your hand throughout the remainder of the class".
        self.scheme.bonus_after_speaking = True
        self.scheme.save()
        speaker = self.student("speaker")
        record_contributions(speaker, self.session, 2, spoke=1, bonus=4)

        values = schemes.measure_values(self.course, self.scheme)

        self.assertEqual(values[speaker.id][ParticipationScheme.VOLUME], 6)

    def test_an_undone_call_is_not_counted(self):
        undone = self.student("undone")
        record_contributions(undone, self.session, 2)
        CourseParticipation.objects.filter(participant=undone).update(
            count_in_calculations=False
        )

        values = schemes.measure_values(self.course, self.scheme)

        self.assertEqual(values[undone.id][ParticipationScheme.VOLUME], 0)
        self.assertEqual(values[undone.id][ParticipationScheme.COVERAGE], 0)
        # And the lecture, with nothing left in it, was not one.
        self.assertEqual(schemes.lecture_scores(self.course, self.scheme), {})

    def test_a_queue_that_does_not_count_is_recorded_but_not_graded(self):
        self.course.included_lists = ["1"]
        self.course.save()
        aside = self.student("aside")
        record_contributions(aside, self.session, 4, participation_list=2)

        values = schemes.measure_values(self.course, self.scheme)

        self.assertEqual(values[aside.id][ParticipationScheme.VOLUME], 0)

    def test_the_settings_page_states_the_rule(self):
        from peer_course import participation_summary

        summary = participation_summary.for_course(self.course)
        self.assertIn(
            "each time the instructor calls on someone while it is up",
            " ".join(summary["lines"]),
        )
