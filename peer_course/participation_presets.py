"""The schemes instructors actually use, expressed as configuration.

Each is a starting point an instructor can then edit, not a locked mode. The
numbers are those the courses run today, so applying a preset to an existing
course reproduces its current rubric rather than changing it.
"""

from .models import MeasureRubric, ParticipationScheme

ATTENDANCE = "attendance"
VOLUME = "volume"
COVERAGE_AND_VOLUME = "coverage_and_volume"
PER_LECTURE = "per_lecture"

PRESETS = {
    ATTENDANCE: {
        "label": "Attendance",
        "describes": "A lecture counts if the student took part in it at all.",
        "scheme": {"lecture_cap": 1, "raise_value": 1.0, "speak_bonus": 0.0,
                   "dropped_lectures": 0},
        "rubrics": [
            {"measure": ParticipationScheme.COVERAGE, "weight": 1.0,
             "absolute_anchors": [{"at": 0, "gives": 0.0}, {"at": 1, "gives": 1.0}],
             "absolute_interpolation": "value",
             "combination": "absolute"},
        ],
    },
    VOLUME: {
        "label": "Contributions across the term",
        "describes": "More contributions earn more credit, up to a target.",
        "scheme": {"lecture_cap": None, "raise_value": 1.0, "speak_bonus": 0.0,
                   "dropped_lectures": 0},
        "rubrics": [
            {"measure": ParticipationScheme.VOLUME, "weight": 1.0,
             "absolute_anchors": [{"at": 0, "gives": 0.0}, {"at": 30, "gives": 1.0}],
             "absolute_interpolation": "value",
             "combination": "absolute"},
        ],
    },
    COVERAGE_AND_VOLUME: {
        # AI 100 as its rubric is written: five marks for turning up and taking
        # part, ten for how much, with the top of the class curved by rank.
        #
        # Seventeen lectures are held. The first is not graded, which is a
        # property of that lecture rather than of the scheme -- clear
        # `counts_for_participation` on it -- leaving sixteen, of which the
        # three weakest are dropped. Attendance is therefore out of thirteen.
        "label": "Attendance and contributions",
        "describes": (
            "Most of the grade for taking part across the term, the rest for "
            "how much, with the top of the class curved."
        ),
        "scheme": {"lecture_cap": None, "raise_value": 1.0, "speak_bonus": 0.0,
                   "lectures_expected": 16, "dropped_lectures": 3,
                   "volume_excludes_covered": True},
        "rubrics": [
            {"measure": ParticipationScheme.COVERAGE, "weight": 5.0,
             "absolute_anchors": [{"at": 0, "gives": 0.0}, {"at": 1, "gives": 1.0}],
             "absolute_interpolation": "value",
             "combination": "absolute"},
            # 0.25 marks per contribution, so the twenty-eighth reaches 7 of
            # the 10 marks contributions are worth. Past that the absolute
            # curve stops climbing and only the class-relative one pays, which
            # is what "below the median you get the score just described"
            # means.
            {"measure": ParticipationScheme.VOLUME, "weight": 10.0,
             "absolute_anchors": [{"at": 0, "gives": 0.0}, {"at": 28, "gives": 0.7}],
             "absolute_interpolation": "value",
             "relative_anchors": [{"at": "median", "gives": 0.7},
                                  {"at": "max", "gives": 1.0}],
             "relative_interpolation": "rank",
             "combination": "better"},
        ],
    },
    PER_LECTURE: {
        # CPSC 430 as its syllabus states it, anchor for anchor -- including
        # "we'll drop the 4 classes in which you have the lowest grades".
        # No credit after speaking: a student who has spoken may be called
        # again, and earns only for raises that are up when someone is called.
        "label": "Each lecture graded on its own",
        "describes": (
            "Every lecture is graded against that lecture's class, so a quiet "
            "week cannot depress a student who took part fully in it. The four "
            "weakest are dropped."
        ),
        "scheme": {"lecture_cap": None, "raise_value": 1.0, "speak_bonus": 0.0,
                   "dropped_lectures": 4},
        "rubrics": [
            {"measure": ParticipationScheme.PER_LECTURE, "weight": 1.0,
             "absolute_anchors": [
                 {"at": 0, "gives": 0.00}, {"at": 1, "gives": 0.50},
                 {"at": 2, "gives": 0.60}, {"at": 3, "gives": 0.65},
                 {"at": 4, "gives": 0.70}, {"at": "median", "gives": 0.80},
             ],
             "absolute_interpolation": "value",
             "relative_anchors": [{"at": "median", "gives": 0.80},
                                  {"at": "max", "gives": 1.00}],
             "relative_interpolation": "value",
             "combination": "better"},
        ],
    },
}


def apply_preset(course, name):
    """Give ``course`` the named scheme, replacing any it already had."""
    if name not in PRESETS:
        raise ValueError(
            f"unknown preset {name!r}; expected one of {', '.join(sorted(PRESETS))}"
        )
    blueprint = PRESETS[name]
    # Every settable field, reset to its model default before the blueprint is
    # applied. Without this a field the new preset does not mention keeps the
    # old preset's value: switching from the term-wide scheme to the
    # per-lecture one left `lectures_expected` at 16, and a course with two
    # lectures graded everyone out of sixteen.
    defaults = {
        field.name: field.get_default()
        for field in ParticipationScheme._meta.get_fields()
        if getattr(field, "editable", False)
        and not field.auto_created
        and field.name not in {"course", "preset"}
    }
    defaults.update({"preset": name, **blueprint["scheme"]})
    scheme, _ = ParticipationScheme.objects.update_or_create(
        course=course, defaults=defaults
    )
    # A scheme is not only how contributions are graded, it is also how they
    # are handed out: whether a student who has spoken can be called again
    # decides who gets the chance to earn anything. A preset that says nothing
    # about it leaves the course as it is.
    course_fields = blueprint.get("course")
    if course_fields:
        for field, value in course_fields.items():
            setattr(course, field, value)
        course.save(update_fields=list(course_fields))
    scheme.rubrics.all().delete()
    for rubric in blueprint["rubrics"]:
        MeasureRubric.objects.create(scheme=scheme, **rubric)
    return scheme


def suggest_preset(course):
    """Which preset a course already behaves like, for migrating settings.

    A cap of one point per session is how attendance has been expressed until
    now, so those courses map to Attendance and everything else to the
    per-lecture rubric they are being graded by today.
    """
    if course.max_hand_raise_points == 1:
        return ATTENDANCE
    return PER_LECTURE
