from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from multiselectfield  import MultiSelectField
import pytz
import logging
logger = logging.getLogger(__name__)

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator

from datetime import timedelta
import json 


DAYS_OF_WEEK = (
    ('mon', 'Monday'),
    ('tue', 'Tuesday'),
    ('wed', 'Wednesday'),
    ('thu', 'Thursday'),
    ('fri', 'Friday'),
    ('sat', 'Saturday'),
    ('sun', 'Sunday'),
)

PARTICIPATION_LIST_CHOICES = [
  ("1", "Green"), ("2", "Blue"), ("3", "Red"), ("4", "Yellow")
]


#: Offered first, because every course on this deployment is at UBC. The rest
#: of the world follows so a visiting course is not stuck.
NEARBY_TIMEZONES = (
    "America/Vancouver",
    "America/Edmonton",
    "America/Winnipeg",
    "America/Toronto",
    "America/Halifax",
    "America/St_Johns",
)


def timezone_choices():
    """Return current time zones without baking tzdata into migration state.

    British Columbia stopped changing its clocks on 8 March 2026 and now keeps
    UTC-7 all year. ``US/Pacific`` still turns back on 1 November, so from that
    date the two zones are an hour apart -- a lecture on the wrong one opens an
    hour after the room fills. Vancouver is therefore first and is the default.
    """
    nearby = [(tz, tz) for tz in NEARBY_TIMEZONES if tz in pytz.common_timezones]
    rest = [(tz, tz) for tz in pytz.common_timezones if tz not in NEARBY_TIMEZONES]
    return [("Canada", nearby), ("Everywhere else", rest)]


class Course(models.Model):
    """Defines a course"""
    displayname = models.CharField(
        "Display Name", db_column="display_name", max_length=128
    )
    """Display name for the course"""
    browsable = models.BooleanField(
        "Visible to Students?", db_column="browsable", db_index=True
    )
    # Compatibility shim: retained so ORM INSERTs satisfy the historical
    # physical NOT NULL column. Agora has no grade-visibility behavior.
    show_student_grades = models.BooleanField(
        "Show Student their grades?",
        db_column="show_student_grades",
        default=False,
        editable=False,
    )
    archived = models.BooleanField("Archived?", db_column="archived", db_index=True)
    # Written when a course is created and read nowhere in this codebase. Kept
    # so the physical column keeps its NOT NULL, but no longer offered as a
    # setting: a switch that does nothing is worse than no switch.
    on_list = models.BooleanField(
        "Is listed as a Regular Courses", db_column="on_list", db_index=True,
        default=True, editable=False,
    )
    created_on = models.DateTimeField("Created On", db_column="created_on", default=timezone.now)
    auto_start = models.BooleanField(
        "Start lectures automatically",
        default=True,
        help_text=(
            "Open a lecture at its scheduled time without anyone pressing "
            "anything. Turn this off to start every lecture by hand."
        ),
    )
    manually_started = models.BooleanField(default=False)
    weight_exponent = models.FloatField(
        "Bias Exponent",
        default=1.0,
        help_text=">1 favors less-spoken more, <1 flattens, negative inverts (spoken first)"    )
    hand_raise_point = models.IntegerField(
      default=1,
      help_text="Points per hand raise"
    )
    max_hand_raise_points = models.IntegerField(
      null=True, blank=True,
      help_text="Maximum hand-raise points per session (leave empty for unlimited)"
    )
    manual_override = models.BooleanField(default=False)
    export_prompt_at_end = models.BooleanField(
        "Offer the export when a lecture ends",
        default=True,
        help_text=(
            "Open the download and email pop-up as soon as a lecture is over."
        ),
    )
    stucode = models.CharField(
        "Student Enroll Code",
        db_column="student_enroll_code",
        max_length=128,
        null=True,
        unique=True,
    )
    """Student enters this auto-generated code to enroll in the course"""
    course_timezone = models.CharField(
        "Course Timezone",
        max_length=32,
        choices=timezone_choices,
        default="America/Vancouver",
        help_text=(
            "Every scheduled time on this course is read in this zone. British "
            "Columbia keeps UTC-7 all year from March 2026, which US/Pacific "
            "does not \u2014 on that zone every lecture after 1 November would "
            "open an hour late."
        ),
    )

    default_session_duration = models.DurationField(
        "Maximum Session Duration",
        default=timedelta(hours=3),
        help_text="Default duration for a manual session (e.g. 30 seconds, 2 minutes, etc.)"
    )

    tascode = models.CharField(
        "TA Enroll Code",
        db_column="ta_enroll_code",
        max_length=128,
        null=True,
        unique=True,
    )
    """TAs can enter this code to gain access to the course"""

    instructor_code = models.CharField(
        "Instructor Enroll Code",
        db_column="instructor_enroll_code",
        max_length=128,
        null=True,
        unique=True,
    )
    """Instructors can enter this code to gain access to the course (not usually used)"""

    student_code_expires_at = models.DateTimeField(null=True, blank=True)
    ta_code_expires_at = models.DateTimeField(null=True, blank=True)
    instructor_code_expires_at = models.DateTimeField(null=True, blank=True)

    # Inert legacy-schema compatibility columns. Keep them in ORM INSERTs until a
    # production-snapshot migration can safely change/drop the physical schema.
    total_late_units = models.IntegerField(blank=True, default=6, editable=False)
    can_tas_see_reviews = models.BooleanField(default=False, editable=False)
    enable_independent_pool = models.BooleanField(default=True, editable=False)

    allow_bonus = models.BooleanField(
        default=True,
        help_text="If true, provides bonus points to student who have spoken after somone else speaks"
    )
    spoken_weight = models.FloatField(
        "Chance of being chosen again in the same class",
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text=(
            "How likely a student who has already spoken this class is to be "
            "chosen again, next to one who has not. 0 means not while anyone "
            "who has not spoken still has their hand up. 0.5 means half as "
            "likely, so those who have not spoken have twice the chance. "
            "1 means this class's history is ignored and every raised hand is "
            "equally likely."
        ),
    )
    """
    One dial across the whole range of policies courses ask for. At 0 a student
    who has spoken is held back until everyone else has had their turn, which
    is what every course did before this field existed. At 1 there is no
    within-class preference at all. Everything between is a graduation between
    the two, and 0.5 is "twice the chance for those who have not spoken".

    Deliberately expressed as the *spoken* student's share rather than the
    unspoken student's multiplier: the multiplier runs to infinity at the
    hard-exclusion end, and a bounded 0-1 dial can express both ends and every
    point between.
    """
    included_lists = MultiSelectField(
      choices=PARTICIPATION_LIST_CHOICES,
      default=["1", "2", "3", "4"],
      blank=True,
      help_text="Lists that count for hand-raises & speaking"
    )
    speaking_point = models.IntegerField(
      default=0,
      validators=[MinValueValidator(0), MaxValueValidator(10)],
      help_text="Each student will receive some number of points when they are chosen to speak, on top of the normal hand raise point."
    )
    agora_mode = models.BooleanField(
        default=False,
        help_text="If True, hides certain items in the navbar (Agora Mode)."
    )

    show_participation_grade = models.BooleanField(
        "Show participation grades by session?",
        default=False,
        help_text=(
            "Show calculated per-session participation grades to students and "
            "on the instructor's participation view."
        ),
    )

    allow_multiple_queues = models.BooleanField(
        default=False,
        help_text=(
            "Allow students to raise their hand in more than one list at the "
            "same time."
        ),
    )

    lecture_guests = models.BooleanField(
        "Let guests join a lecture",
        default=False,
        help_text=(
            "Show a six-digit code during each lecture that lets anyone join "
            "it with just a name. Guests can raise hands, be called on and "
            "answer polls, but never count in grades, exports or attendance, "
            "and are deleted when the lecture ends."
        ),
    )

    classroom_microphone = models.BooleanField(
        "Classroom microphone",
        default=False,
        help_text=(
            "Let the student you call on speak through their phone, played "
            "on the room speakers from your laptop. Off by default: when off, "
            "students see no microphone panel and no audio is routed."
        ),
    )


    # --- demonstration classes ------------------------------------------
    # A sandbox is a throwaway class an instructor makes to try Agora or to
    # show it in a meeting. It is never listed, it expires, and everything it
    # owns is deleted with it.
    is_sandbox = models.BooleanField(
        "A demonstration class",
        default=False,
        db_index=True,
        help_text=(
            "Made to try Agora out or to show it. Expires on its own, and "
            "everything in it goes with it."
        ),
        editable=False,
    )
    sandbox_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    join_code = models.CharField(
        max_length=12,
        blank=True,
        default="",
        db_index=True,
        editable=False,
        help_text=(
            "Six digits, short enough to read to a room. Only a demonstration "
            "class has one; a real course uses its enrolment codes."
        ),
    )

    color_config = models.TextField(
        "Color Config (JSON)",
        null=True,
        blank=True,
        help_text="Stores JSON object for custom color names (green,blue,red,yellow)."
    )

    class Meta:
        db_table = "course"

    def __str__(self):
        return "%s" % (self.displayname)

    def has_ta(self):
        return self.members.filter(role="ta").exists()

    #: Which colour backs which participation list. The list identity lives in
    #: a column name on CourseMember, so this is the only place that mapping is
    #: written down.
    QUEUE_COLOURS = {1: "green", 2: "blue", 3: "red", 4: "yellow"}

    def queue_labels(self):
        """``{position: name}`` for every list.

        Reads `color_config`, which has always been the store for these names
        and is edited from the dashboard. A course that renamed a list --
        "Participate", "Q&A", "Questions" -- gets those names here too, rather
        than a second set competing with them.
        """
        configured = self.get_color_config_dict()
        return {
            position: (configured.get(colour) or colour).strip() or colour.capitalize()
            for position, colour in self.QUEUE_COLOURS.items()
        }

    def list_counts_for_points(self, list_id):
        """Whether a participation list is included in point calculations."""
        return str(list_id) in {str(value) for value in self.included_lists}

    def get_color_config_dict(self):
        """
        Returns a dict of color names for this course.
        Example: {"green": "Green", "blue": "Blue", "red": "Red", "yellow": "Yellow"}
        If color_config is empty or invalid, we return defaults.
        """
        default_colors = {
            "green": "Green",
            "blue": "Blue",
            "red": "Red",
            "yellow": "Yellow"
        }
        if not self.color_config:
            # logger.warning(f"Color config is empty for course {self.id}. Using defaults.")
            return default_colors

        try:
            data = json.loads(self.color_config)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Invalid color config for course {self.id}: {e}. Using defaults.")
            return default_colors

        for key, val in default_colors.items():
            if key not in data:
                # logger.info(f"Missing key '{key}' in color config for course {self.id}. Using default value.")
                data.setdefault(key, val)
            configured = data.get(key)
            if (
                not isinstance(configured, str)
                or not configured.strip()
                or len(configured) > 15
                or any(character in configured for character in "<>&")
                or any(ord(character) < 32 for character in configured)
            ):
                data[key] = val

        return data

    def tas(self):
        return self.members.filter(role="ta")
    





#: Roles that carry an instructor's privileges. ``superuser`` is a course role
#: held by thirteen members in production, distinct from Django's own
#: ``User.is_superuser`` flag. Every check used to compare against the literal
#: "instructor", so a course superuser silently had fewer privileges than the
#: instructors they were meant to outrank.
INSTRUCTOR_ROLES = ("instructor", "superuser")

#: Who takes part in a live lecture: raising hands, being called on, polls.
#: A ``guest`` joined one lecture with its code and is deleted when it ends
#: (see ``lecture_guests``). Grades, exports, attendance and rosters ask for
#: ``role="student"`` and so never see a guest; only live paths use this.
LEARNER_ROLES = ("student", "guest")

#: Anyone who runs the course, as opposed to taking it.
STAFF_ROLES = INSTRUCTOR_ROLES + ("ta",)


def display_role(role):
    if role == "ta":
        return "TA"
    return role.capitalize()


class CourseMember(models.Model):
    """Each student/TA/instructor corresponds to a CourseMember object"""

    course = models.ForeignKey(Course, related_name="members", on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name="memberships", on_delete=models.CASCADE)

    role = models.CharField(
        "User Type", db_column="role", max_length=128, db_index=True
    )

    # Inert legacy-schema compatibility columns; see Course shims above.
    is_independent = models.BooleanField(
        "is_independent?",
        db_column="is_independent",
        default=False,
        db_index=True,
        editable=False,
    )
    time_is_independent_changed = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    active = models.BooleanField(default=True, blank=True, db_index=True)
    """
    Not deleting members from DB, since it automatically removes all of their submissions/reviews.
    (active == False) indicates that the member has been removed from the course.
    """

    qualified = models.BooleanField(default=True, blank=True, editable=False)
    upper_confidence_bound = models.FloatField(
        "Upper confidence bound",
        db_column="upperconfidencebound",
        null=True,
        default=1.0,
        editable=False,
    )
    markingload = models.FloatField(
        "Marking Load",
        db_column="markingload",
        null=True,
        default=0.0,
        editable=False,
    )
    lower_confidence_bound = models.FloatField(
        "Lower confidence bound",
        db_column="lowerconfidencebound",
        null=True,
        default=0.0,
        editable=False,
    )

    hand_up = models.BooleanField(
        "hand_up", db_column="hand_up", default=False, db_index=True
    )
    hand_up_list_2 = models.BooleanField(
        "hand_up_list_2", db_column="hand_up_list_2", default=False, db_index=True
    )
    hand_up_list_3 = models.BooleanField(
        "hand_up_list_3", db_column="hand_up_list_3", default=False, db_index=True
    )
    hand_up_list_4 = models.BooleanField(
        "hand_up_list_4", db_column="hand_up_list_4", default=False, db_index=True
    )
    spoken = models.BooleanField(
        "spoken", db_column="spoken", default=False, db_index=True
    )
    time_spoken = models.DateTimeField("time_spoken", db_column="time_spoken", default=timezone.now)

    participation_points = models.IntegerField(
        "participation_points", db_column='participation_points', 
        null=True, default=0
    )

    regular_points = models.IntegerField(
        "regular_points",
        db_column="regular_points",
        null=True,
        default=0,
        editable=False,
    )

    # Someone who joined a demonstration class with its code and a name, with
    # no account of their own. Refused everything outside that class.
    is_sandbox_guest = models.BooleanField(
        default=False, db_index=True, editable=False
    )

    first_hand_up = models.BooleanField(
        "first_hand_up", db_column="first_hand_up", default=True, db_index=True
    )

    class Meta:
        db_table = "course_member"
        unique_together = ("course", "user")

    def __str__(self):
        return "CourseMember %s in %s as %s" % (
            self.get_user_fullname(),
            self.course,
            self.role,
        )
    


    def display(self):
        return "%s (%s in %s)" % (
            self.get_user_fullname(),
            self.display_role(),
            self.course.displayname,
        )

    def is_TA(self):
        return self.role == "ta"

    def is_instructor(self):
        return self.role in INSTRUCTOR_ROLES

    def is_staff(self):
        return self.role in STAFF_ROLES

    def display_role(self):
        return display_role(self.role)

    def get_user_fullname(self):
        """The name to show for this person.

        A preferred name if they have set one, otherwise the registrar's.

        The old implementation was ``first_name.capitalize() + " " +
        last_name.capitalize()``. str.capitalize lowercases everything after
        the first letter, so it rendered McDonald as "Mcdonald", de Silva as
        "De silva" and MARIE-CLAIRE as "Marie-claire" -- on the queue the
        instructor reads names off. Names are shown as their owner wrote
        them.
        """
        profile = getattr(self.user, "profile", None)
        if profile is not None:
            chosen = profile.display_name()
            if chosen:
                return chosen
        return " ".join(
            part
            for part in (
                (self.user.first_name or "").strip(),
                (self.user.last_name or "").strip(),
            )
            if part
        )

    def display_name_parts(self):
        """``(first, last)`` as this person wants to be addressed.

        The preferred name from their profile where set, otherwise the
        registrar's. For the queue lists the lecture screen builds, which
        carry the two parts separately.
        """
        profile = getattr(self.user, "profile", None)
        first = (
            getattr(profile, "preferred_first_name", "") or self.user.first_name or ""
        ).strip()
        last = (
            getattr(profile, "preferred_last_name", "") or self.user.last_name or ""
        ).strip()
        return first, last

    def get_registrar_fullname(self):
        """The name UBC has, ignoring any preferred name.

        For anything that has to reconcile against the registrar rather than
        address a person.
        """
        return " ".join(
            part
            for part in (
                (self.user.first_name or "").strip(),
                (self.user.last_name or "").strip(),
            )
            if part
        )

    def get_user_id(self):
        # FIXME: if we change how we store student ID
        return self.user.username

class CourseSession(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="sessions")

    ended = models.BooleanField(
        default=False,
        help_text="True if the session has been manually or automatically ended."
    )

    cancel_next_session = models.BooleanField(
    "Cancel the next session",
    default=False,
    help_text="If True, skip spawning the next session just one time."
    )

    name = models.CharField(
        max_length=120,
        blank=True,
        help_text=(
            'What this lecture is called, such as "Week 3: normalisation". '
            "Empty shows the date."
        ),
    )
    counts_for_participation = models.BooleanField(
        "Counts toward participation grades",
        default=True,
        help_text=(
            "Clear this for a lecture that should not be graded -- a first "
            "meeting, a guest lecture, or one that was cancelled after hands "
            "had already been raised. Excluded lectures keep their records but "
            "count for nobody, neither as attendance nor as absence."
        ),
    )
    cancelled = models.BooleanField(
        "No lecture on this day",
        default=False,
        help_text=(
            "There is no lecture on this date -- a holiday, a reading break, "
            "or one called off. It cannot be started, it counts for nobody, "
            "and nobody is absent from it. The weekly pattern will not put a "
            "lecture here again."
        ),
    )
    is_template = models.BooleanField(
        default=False,
        help_text="If True, this row is a recurring template, not a single-day session."
    )
    actual_end_time = models.TimeField(
        "Actual End Time (PST)",
        null=True,
        blank=True,    
    )

    session_date = models.DateField(
        "Course Start Date",
        null=True,
        blank=True,
        help_text="Real session date. Empty if is_template=True."
    )
    session_end_date = models.DateField(
        "Course End Date",
        null=True,
        blank=True,
        help_text="Indicate when the course ends."
    )

    start_time_pst = models.TimeField(
        "Session Start Time (PST)",
        null=True,
        blank=True
    )
    end_time_pst = models.TimeField(
        "Session End Time (PST)",
        null=True,
        blank=True
    )
    active_days = MultiSelectField(
        choices=DAYS_OF_WEEK,
        blank=True,
        max_length=27,
        help_text="For templates, which days of the week is this session active?"
    )


    @property
    def display_name(self):
        """What to call this session wherever one is listed.

        Views built ``f"Session on {date}"`` in three places and the
        participation page built a fourth variant. One property means a renamed
        lecture is renamed everywhere.
        """
        if self.name.strip():
            return self.name.strip()
        if self.is_template:
            days = self.active_days or ""
            when = days.replace(",", ", ") if days else "no days set"
            return f"Weekly: {when}"
        if self.session_date:
            return self.session_date.strftime("%-d %b %Y")
        return "Session"
    class Meta:
        db_table = "course_session"
        constraints = [
            models.UniqueConstraint(
                fields=("course", "session_date", "start_time_pst", "end_time_pst"),
                condition=models.Q(is_template=False),
                name="unique_agora_real_session_slot",
            )
        ]

    def __str__(self):
        if self.is_template:
            return f"[TEMPLATE] {self.course.displayname} ({self.active_days}) {self.start_time_pst}-{self.end_time_pst}"
        else:
            return f"Session {self.pk} for {self.course.displayname}: {self.session_date} {self.start_time_pst}-{self.end_time_pst}"


class SchedulerLease(models.Model):
    """Short database lease that prevents overlapping maintenance schedulers."""

    name = models.CharField(max_length=100, primary_key=True)
    owner = models.CharField(max_length=64)
    expires_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scheduler_lease"



class HandRaise(models.Model):
    """One student's hand, from the moment it goes up to the moment it comes down.

    Agora has only ever stored per-session point totals, so the facts a grading
    scheme needs -- how many times a student raised their hand, and in how many
    separate lectures -- were computed once and thrown away. Points remain
    authoritative for grading today; this records the underlying events so a
    scheme can be changed later and applied to work already done.

    A row is roughly 180 bytes. A term is single-digit megabytes, against the
    two gigabytes of duplicated log lines that were mistaken for a data problem.
    """

    class Resolution(models.TextChoices):
        SPOKE = "spoke", "Chosen to speak"
        LOWERED = "lowered", "Lowered by the student"
        CLEARED = "cleared", "Cleared by the instructor"
        MOVED = "moved", "Moved to another list"
        ATTENDANCE = "attendance", "Cleared when attendance closed"

    member = models.ForeignKey(
        CourseMember, on_delete=models.CASCADE, related_name="hand_raises"
    )
    # A session can be deleted without destroying the record that a hand went
    # up; the timestamp still places it.
    session = models.ForeignKey(
        "CourseSession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hand_raises",
    )
    participation_list = models.PositiveSmallIntegerField()
    raised_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(
        max_length=16, blank=True, choices=Resolution.choices
    )

    class Meta:
        db_table = "course_hand_raise"
        constraints = [
            # A hand is either up or down, so one unresolved row per list is the
            # invariant. A partial unique index states it in the schema rather
            # than trusting twenty duplicated view branches to maintain it.
            models.UniqueConstraint(
                fields=("member", "participation_list"),
                condition=models.Q(resolved_at__isnull=True),
                name="unique_open_hand_raise",
            ),
        ]
        indexes = [
            models.Index(fields=("session", "member"), name="hand_raise_session_idx"),
            models.Index(fields=("member", "raised_at"), name="hand_raise_member_idx"),
        ]

    def __str__(self):
        state = self.resolution or "open"
        return f"HandRaise({self.member_id}, list {self.participation_list}, {state})"

    @property
    def is_open(self):
        return self.resolved_at is None


class AttendanceCheck(models.Model):
    """One roll call: every list opens, the instructor names one out loud.

    A student in the room hears which list it is; someone following along from
    outside does not, and has to guess. A hand in any other list is not proof of
    absence -- people mishear -- so it is put in front of the instructor to be
    called on and settled, rather than marked absent automatically.

    ``announced_list`` must never reach a student's browser. It is only sent in
    answer to an instructor's request, never in a broadcast.
    """

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="attendance_checks"
    )
    session = models.ForeignKey(
        "CourseSession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_checks",
    )
    announced_list = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Which lists were open before every list was opened for the roll call, so
    # closing it puts the lecture back the way the instructor had it.
    lists_open_before = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "course_attendance_check"
        ordering = ("started_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("course",),
                condition=models.Q(closed_at__isnull=True),
                name="one_open_attendance_check_per_course",
            ),
        ]

    def __str__(self):
        state = "open" if self.closed_at is None else "closed"
        return f"AttendanceCheck({self.course_id}, list {self.announced_list}, {state})"

    @property
    def is_open(self):
        return self.closed_at is None


class AttendanceRecord(models.Model):
    """Where one student's hand was when the roll call closed.

    Only students with a hand up get a row. Anyone enrolled without one was
    absent, which the roster already says without a row per student.
    """

    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        # A hand in the wrong list; the instructor called on them and they
        # answered.
        CONFIRMED = "confirmed", "In class (confirmed)"
        # A hand in the wrong list and nobody answered: to follow up.
        NOT_IN_CLASS = "not_in_class", "Not in class"
        UNREVIEWED = "unreviewed", "Wrong list, not yet checked"
        # Arrived late, phone died: added by hand from the lecture page.
        ADDED = "added", "Marked present by the instructor"

    #: Statuses that count as attending.
    ATTENDED = (Status.PRESENT, Status.CONFIRMED, Status.ADDED)

    roll_call = models.ForeignKey(
        AttendanceCheck, on_delete=models.CASCADE, related_name="records"
    )
    member = models.ForeignKey(
        CourseMember, on_delete=models.CASCADE, related_name="attendance_records"
    )
    lists_raised = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "course_attendance_record"
        constraints = [
            models.UniqueConstraint(
                fields=("roll_call", "member"), name="one_attendance_record_per_check"
            ),
        ]

    def __str__(self):
        return f"AttendanceRecord({self.member_id}, {self.status})"

    @property
    def attended(self):
        return self.status in self.ATTENDED


class ParticipationScheme(models.Model):
    """How one course turns hand raises into a participation grade.

    Two questions decide every scheme Agora runs. What earns credit inside one
    lecture, and how lectures add up over a term. Attendance, CPSC 320,
    CPSC 430 and the term-wide scheme drafted for AI 100 differ only in the
    answers, so they are configuration rather than four code paths.

    A course without one keeps the behaviour it has today.
    """

    COVERAGE = "coverage"
    VOLUME = "volume"
    PER_LECTURE = "per_lecture"

    course = models.OneToOneField(
        Course, on_delete=models.CASCADE, related_name="participation_scheme"
    )
    preset = models.CharField(max_length=32, blank=True)

    # -- what earns credit inside one lecture --------------------------------
    raise_value = models.FloatField(
        default=1.0, help_text="Credit for one hand raise."
    )
    speak_bonus = models.FloatField(
        default=0.0,
        help_text="Extra credit when a raise ends in being chosen to speak.",
    )
    lecture_cap = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Most contributions one lecture can count, or empty for no limit. "
            "A cap of 1 makes every lecture pass or fail, which is attendance."
        ),
    )

    # -- how lectures add up over a term -------------------------------------
    lectures_expected = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Lectures the term is graded against; empty counts those held.",
    )
    dropped_lectures = models.PositiveIntegerField(
        default=0, help_text="Lowest lectures ignored, to allow for absence."
    )
    bonus_after_speaking = models.BooleanField(
        "Keep earning after speaking",
        default=False,
        help_text=(
            "Once a student has spoken in a lecture, they keep collecting "
            "contributions as other students speak."
        ),
    )
    volume_excludes_covered = models.BooleanField(
        "Contributions exclude the one that earned attendance",
        default=False,
        help_text=(
            "Leave one contribution per lecture attended out of the term "
            "total, because it has already been counted as attendance. Only "
            "meaningful when attendance and contributions are both graded."
        ),
    )

    def __str__(self):
        return f"Participation scheme for {self.course.displayname}"


class MeasureRubric(models.Model):
    """One measure's share of the grade, and the curve that awards it.

    Anchors are stored as ``[{"at": 30, "gives": 0.6}, ...]`` where ``at`` is a
    count or a class statistic such as ``"median"``, and ``gives`` is a fraction
    of this measure's marks. Two curves may be given: an absolute one that a
    student can be told in advance, and a class-relative one for the top of the
    distribution. ``combination`` decides how they resolve.
    """

    MEASURES = (
        (ParticipationScheme.COVERAGE, "Attendance"),
        (ParticipationScheme.VOLUME, "Contributions"),
        (ParticipationScheme.PER_LECTURE, "Each lecture on its own"),
    )
    INTERPOLATIONS = (
        ("value", "By value"),
        ("rank", "By rank in the class"),
        ("step", "Stepped"),
    )
    COMBINATIONS = (
        ("better", "The better of the two"),
        ("absolute", "Absolute only"),
        ("relative", "Class-relative only"),
    )

    scheme = models.ForeignKey(
        ParticipationScheme, on_delete=models.CASCADE, related_name="rubrics"
    )
    measure = models.CharField(max_length=16, choices=MEASURES)
    weight = models.FloatField(
        default=0.0, help_text="This measure's share of the participation grade."
    )
    absolute_anchors = models.JSONField(default=list, blank=True)
    absolute_interpolation = models.CharField(
        max_length=8, choices=INTERPOLATIONS, default="value"
    )
    relative_anchors = models.JSONField(default=list, blank=True)
    relative_interpolation = models.CharField(
        max_length=8, choices=INTERPOLATIONS, default="rank"
    )
    combination = models.CharField(
        max_length=8, choices=COMBINATIONS, default="better"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("scheme", "measure"), name="one_rubric_per_measure"
            )
        ]


class EarlierCurve(models.Model):
    """The curves a per-lecture rubric graded lectures with before ``until``.

    A per-lecture rubric grades every lecture whenever a grade is computed, so
    editing it re-grades the lectures already held. A course that changes its
    table mid-term for the lectures still to come keeps the old curves here,
    and they go on grading the lectures dated before ``until``.
    """

    rubric = models.ForeignKey(
        MeasureRubric, on_delete=models.CASCADE, related_name="earlier_curves"
    )
    until = models.DateField(
        help_text="Lectures dated before this are graded with these curves."
    )
    absolute_anchors = models.JSONField(default=list, blank=True)
    absolute_interpolation = models.CharField(
        max_length=8, choices=MeasureRubric.INTERPOLATIONS, default="value"
    )
    relative_anchors = models.JSONField(default=list, blank=True)
    relative_interpolation = models.CharField(
        max_length=8, choices=MeasureRubric.INTERPOLATIONS, default="rank"
    )
    combination = models.CharField(
        max_length=8, choices=MeasureRubric.COMBINATIONS, default="better"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("rubric", "until"), name="one_earlier_curve_per_date"
            )
        ]

    def __str__(self):
        return f"{self.get_measure_display()} ({self.weight:g})"


class CourseParticipation(models.Model):
    participant = models.ForeignKey(
        CourseMember, related_name="participant", on_delete=models.CASCADE
    )

    session = models.ForeignKey(
        "CourseSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="participations"
    )

    bonus_points = models.IntegerField(null=True, db_column="bonus_points" , default=0)

    time_participated = models.DateTimeField("time_participated", db_column="time_participated", default=timezone.now)
    
    participation_list = models.IntegerField(
        "participation_list", db_column='participation_list', null=True, default=0
    )
    participation_points_gained = models.IntegerField(
        "participation_points_gained", db_column='participation_points_gained', null=True, default=0
    )
    spoke_upon_participation = models.BooleanField(
        "spoke_upon_participation", db_column="spoke_upon_participation", default=False, db_index=True
    )

    count_in_calculations = models.BooleanField(
        "count_in_calculations", db_column="count_in_calculations", default=True, db_index=True
    )
    real_participation = models.BooleanField(
        "real_participation", db_column="real_participation", default=True, db_index=True
    )

    class Meta:
        db_table = "course_participation"

    def __str__(self):
        return f"CourseParticipation for {self.participant} on list {self.participation_list}"
