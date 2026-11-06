from django.db import migrations, models


class Migration(migrations.Migration):
    """Restore two live Agora course settings on the hardened line.

    The deployed instance carries these as uncommitted migrations 0071 and
    0072, both branching from 0070. Those numbers are already taken here by
    the enrollment-code and scheduler-lease work, and Agora deploys onto a new
    PostgreSQL database rather than upgrading the SQLite one, so the fields are
    reintroduced linearly instead of merged.
    """

    dependencies = [
        ("peer_course", "0074_mark_mta_schema_shims_inert"),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="allow_multiple_queues",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Allow students to raise their hand in more than one list "
                    "at the same time."
                ),
            ),
        ),
        migrations.AddField(
            model_name="course",
            name="show_participation_grade",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Show calculated per-session participation grades to "
                    "students and on the instructor's participation view."
                ),
                verbose_name="Show participation grades by session?",
            ),
        ),
    ]
