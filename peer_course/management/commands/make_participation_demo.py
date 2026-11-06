"""Build demonstration courses with invented participation, and remove them.

The participation charts read `HandRaise`, and those have only been recorded
since 2026-09-04, so a real past course shows an empty page. This makes two
courses with plausible data instead -- one graded lecture by lecture, one on
attendance plus contributions -- so both ways of presenting a grade can be
looked at side by side.

Everything it creates is named with the same prefix and removed by
``--remove``. It touches no existing course, and the accounts it makes are
inactive and have no usable password, so nothing here can be signed in to.
"""

import random
from datetime import date, time, timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from peer_course import participation_presets as presets
from peer_course.models import (
    Course,
    CourseMember,
    CourseParticipation,
    CourseSession,
    HandRaise,
)

PREFIX = "DEMO "
#: Distinct enough that a real account cannot collide with one of these.
STUDENT_PREFIX = "demo-student-"

NAMES = [
    "Avery Chen", "Bo Nakamura", "Cass Oyelaran", "Devi Raman", "Emre Aydin",
    "Fen Zhao", "Gio Ferrara", "Hana Kobayashi", "Ines Moreau", "Jae Park",
    "Kira Volkov", "Luca Rossi", "Mira Haddad", "Nils Berg", "Ola Adeyemi",
    "Pia Lindqvist", "Quan Vu", "Rosa Delgado", "Sami Farah", "Tomas Novak",
    "Uma Krishnan", "Vera Ilic", "Wren Okafor", "Xiu Lam", "Yuki Sato",
    "Zane Mbeki", "Ada Whitfield", "Bram de Vries", "Cleo Marchetti",
    "Dara O'Connell",
]


class Command(BaseCommand):
    help = "Create or remove demonstration courses with invented participation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--instructor",
            help="Username to enrol as instructor on the demo courses.",
        )
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete the demo courses and their invented accounts.",
        )
        parser.add_argument("--lectures", type=int, default=12)
        parser.add_argument("--students", type=int, default=len(NAMES))
        parser.add_argument(
            "--seed", type=int, default=20260904,
            help="Fixed so the same data comes back on a rebuild.",
        )

    def handle(self, *args, **options):
        if options["remove"]:
            return self._remove()
        if not options["instructor"]:
            raise CommandError("--instructor is required unless removing.")
        return self._create(options)

    # -- removal ---------------------------------------------------------

    def _remove(self):
        courses = Course.objects.filter(displayname__startswith=PREFIX)
        names = list(courses.values_list("displayname", flat=True))
        with transaction.atomic():
            courses.delete()
            accounts = User.objects.filter(username__startswith=STUDENT_PREFIX)
            count = accounts.count()
            accounts.delete()
        for name in names:
            self.stdout.write(f"removed {name!r}")
        self.stdout.write(
            self.style.SUCCESS(
                f"removed {len(names)} course(s) and {count} demo account(s)"
            )
        )

    # -- creation --------------------------------------------------------

    def _create(self, options):
        try:
            instructor = User.objects.get(username=options["instructor"])
        except User.DoesNotExist as error:
            raise CommandError(
                f"no account named {options['instructor']!r}"
            ) from error

        rng = random.Random(options["seed"])
        built = []
        with transaction.atomic():
            for label, preset in (
                ("per-lecture grading", presets.PER_LECTURE),
                ("attendance and contributions", presets.COVERAGE_AND_VOLUME),
            ):
                course = self._build(
                    f"{PREFIX}— {label}",
                    preset,
                    instructor,
                    options["lectures"],
                    options["students"],
                    rng,
                )
                built.append(course)

        for course in built:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{course.displayname}: /course/{course.id}/ "
                    f"({course.members.filter(role='student').count()} students, "
                    f"{course.sessions.filter(is_template=False).count()} lectures)"
                )
            )
        self.stdout.write("remove them again with --remove")

    def _build(self, name, preset, instructor, lectures, students, rng):
        course = Course.objects.create(
            displayname=name,
            browsable=False,
            archived=False,
            on_list=True,
            agora_mode=True,
            show_participation_grade=True,
            hand_raise_point=1,
            speaking_point=0,
        )
        CourseMember.objects.create(
            course=course, user=instructor, role="instructor"
        )
        scheme = presets.apply_preset(course, preset)
        if scheme.lectures_expected:
            # The term-wide preset assumes sixteen lectures. Left alone, a demo
            # with twelve caps coverage below full marks for everybody, which
            # reads as a fault rather than as a term still in progress.
            scheme.lectures_expected = lectures - 1  # the first is excluded
            scheme.save(update_fields=["lectures_expected"])
        course.refresh_from_db()

        sessions = [
            CourseSession.objects.create(
                course=course,
                session_date=date(2026, 1, 6) + timedelta(days=3 * index),
                start_time_pst=time(10, 0),
                end_time_pst=time(11, 0),
                is_template=False,
                ended=True,
                name="Introduction" if index == 0 else "",
            )
            for index in range(lectures)
        ]
        # The first meeting is the case the exclusion exists for.
        sessions[0].counts_for_participation = False
        sessions[0].save(update_fields=["counts_for_participation"])

        members = []
        for index in range(min(students, len(NAMES))):
            full = NAMES[index]
            first, _, last = full.partition(" ")
            user = User.objects.create(
                username=f"{STUDENT_PREFIX}{course.id}-{index}",
                first_name=first,
                last_name=last,
                # Inactive with no usable password: these cannot be signed in to.
                is_active=False,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            members.append(
                CourseMember.objects.create(
                    course=course, user=user, role="student"
                )
            )

        # A plausible spread: a few who speak constantly, a long middle, and
        # some who rarely do. Attendance is imperfect for everyone.
        for member in members:
            eagerness = rng.choices(
                [0.15, 0.5, 0.85], weights=[3, 6, 2]
            )[0]
            for session in sessions:
                if rng.random() > 0.82 + eagerness * 0.12:
                    continue  # absent
                raises = max(0, int(rng.gauss(eagerness * 7, 2.2)))
                now = timezone.make_aware(
                    timezone.datetime.combine(
                        session.session_date, time(10, 5)
                    )
                )
                for order in range(raises):
                    spoke = rng.random() < 0.35
                    when = now + timedelta(minutes=order * 2)
                    queue = rng.choices([1, 2, 3], weights=[6, 2, 1])[0]
                    HandRaise.objects.create(
                        member=member,
                        session=session,
                        participation_list=queue,
                        raised_at=when,
                        resolved_at=when + timedelta(minutes=1),
                        resolution=(
                            HandRaise.Resolution.SPOKE
                            if spoke
                            else HandRaise.Resolution.LOWERED
                        ),
                    )
                    # HandRaise is the event log; grades are computed from
                    # CourseParticipation. Writing only the former gave every
                    # demo course a class where all thirty students scored 0%,
                    # which is a poor demonstration of a grading scheme.
                    CourseParticipation.objects.create(
                        participant=member,
                        session=session,
                        time_participated=when,
                        participation_list=queue,
                        participation_points_gained=course.hand_raise_point,
                        spoke_upon_participation=spoke,
                        real_participation=True,
                        count_in_calculations=True,
                    )
        return course
