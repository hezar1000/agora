from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    """Hide legacy MTA columns without changing or omitting physical data."""

    dependencies = [("peer_course", "0073_stable_timezone_choices")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="course",
                    name="show_student_grades",
                    field=models.BooleanField(
                        db_column="show_student_grades",
                        default=False,
                        editable=False,
                        verbose_name="Show Student their grades?",
                    ),
                ),
                migrations.AlterField(
                    model_name="course",
                    name="total_late_units",
                    field=models.IntegerField(
                        blank=True,
                        default=6,
                        editable=False,
                    ),
                ),
                migrations.AlterField(
                    model_name="course",
                    name="can_tas_see_reviews",
                    field=models.BooleanField(default=False, editable=False),
                ),
                migrations.AlterField(
                    model_name="course",
                    name="enable_independent_pool",
                    field=models.BooleanField(default=True, editable=False),
                ),
                migrations.AlterField(
                    model_name="coursemember",
                    name="is_independent",
                    field=models.BooleanField(
                        db_column="is_independent",
                        db_index=True,
                        default=False,
                        editable=False,
                        verbose_name="is_independent?",
                    ),
                ),
                migrations.AlterField(
                    model_name="coursemember",
                    name="time_is_independent_changed",
                    field=models.DateTimeField(
                        default=timezone.now,
                        editable=False,
                    ),
                ),
                migrations.AlterField(
                    model_name="coursemember",
                    name="qualified",
                    field=models.BooleanField(
                        blank=True,
                        default=True,
                        editable=False,
                    ),
                ),
                migrations.AlterField(
                    model_name="coursemember",
                    name="upper_confidence_bound",
                    field=models.FloatField(
                        db_column="upperconfidencebound",
                        default=1.0,
                        editable=False,
                        null=True,
                        verbose_name="Upper confidence bound",
                    ),
                ),
                migrations.AlterField(
                    model_name="coursemember",
                    name="markingload",
                    field=models.FloatField(
                        db_column="markingload",
                        default=0.0,
                        editable=False,
                        null=True,
                        verbose_name="Marking Load",
                    ),
                ),
                migrations.AlterField(
                    model_name="coursemember",
                    name="lower_confidence_bound",
                    field=models.FloatField(
                        db_column="lowerconfidencebound",
                        default=0.0,
                        editable=False,
                        null=True,
                        verbose_name="Lower confidence bound",
                    ),
                ),
                migrations.AlterField(
                    model_name="coursemember",
                    name="regular_points",
                    field=models.IntegerField(
                        db_column="regular_points",
                        default=0,
                        editable=False,
                        null=True,
                        verbose_name="regular_points",
                    ),
                ),
            ],
        )
    ]
