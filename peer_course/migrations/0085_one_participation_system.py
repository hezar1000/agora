"""Fold the course's participation columns into its scheme.

Two sets of settings held the same four numbers, read by different code. The
course columns are the ones that have actually been driving lectures --
schemes are three days old and no term has been graded with one -- so they are
what a course's behaviour must be preserved from, not the other way round.

Every course gets a scheme if it lacks one, seeded from the rubric it already
grades by, and every scheme takes the course's numbers. A course whose two
sets already agreed, which is nearly all of them, is untouched in effect.
"""

from django.db import migrations, models


def fold_into_scheme(apps, schema_editor):
    Course = apps.get_model("peer_course", "Course")
    ParticipationScheme = apps.get_model("peer_course", "ParticipationScheme")
    MeasureRubric = apps.get_model("peer_course", "MeasureRubric")

    for course in Course.objects.all():
        scheme = ParticipationScheme.objects.filter(course=course).first()
        if scheme is None:
            # The same shapes participation_presets seeds, written out here
            # because a migration must not import code that will move.
            attendance = course.max_hand_raise_points == 1
            scheme = ParticipationScheme.objects.create(
                course=course,
                preset="attendance" if attendance else "per_lecture",
                lecture_cap=1 if attendance else None,
                raise_value=1.0,
                speak_bonus=0.0,
                dropped_lectures=0,
            )
            if attendance:
                MeasureRubric.objects.create(
                    scheme=scheme, measure="coverage", weight=1.0,
                    absolute_anchors=[{"at": 0, "gives": 0.0},
                                      {"at": 1, "gives": 1.0}],
                    absolute_interpolation="value", combination="absolute",
                )
            else:
                MeasureRubric.objects.create(
                    scheme=scheme, measure="per_lecture", weight=1.0,
                    absolute_anchors=[
                        {"at": 0, "gives": 0.00}, {"at": 1, "gives": 0.50},
                        {"at": 2, "gives": 0.60}, {"at": 3, "gives": 0.65},
                        {"at": 4, "gives": 0.70}, {"at": "median", "gives": 0.80},
                    ],
                    absolute_interpolation="value",
                    relative_anchors=[{"at": "median", "gives": 0.80},
                                      {"at": "max", "gives": 1.00}],
                    relative_interpolation="value", combination="better",
                )

        scheme.raise_value = course.hand_raise_point
        scheme.speak_bonus = course.speaking_point
        scheme.lecture_cap = course.max_hand_raise_points
        scheme.bonus_after_speaking = course.allow_bonus
        scheme.save(update_fields=[
            "raise_value", "speak_bonus", "lecture_cap", "bonus_after_speaking",
        ])


def unfold(apps, schema_editor):
    """Put the numbers back on the course, so the migration reverses."""
    ParticipationScheme = apps.get_model("peer_course", "ParticipationScheme")
    for scheme in ParticipationScheme.objects.select_related("course"):
        course = scheme.course
        course.hand_raise_point = int(scheme.raise_value)
        course.speaking_point = int(scheme.speak_bonus)
        course.max_hand_raise_points = scheme.lecture_cap
        course.allow_bonus = scheme.bonus_after_speaking
        course.save(update_fields=[
            "hand_raise_point", "speaking_point", "max_hand_raise_points",
            "allow_bonus",
        ])


class Migration(migrations.Migration):

    dependencies = [
        ("peer_course", "0084_coursesession_cancelled_alter_measurerubric_measure"),
    ]

    operations = [
        migrations.AddField(
            model_name="participationscheme",
            name="bonus_after_speaking",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Once a student has spoken in a lecture, they keep "
                    "collecting contributions as other students speak."
                ),
                verbose_name="Keep earning after speaking",
            ),
        ),
        migrations.RunPython(fold_into_scheme, unfold),
    ]
