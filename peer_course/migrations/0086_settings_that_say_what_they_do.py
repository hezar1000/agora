"""Rename two settings to what they turn on, and move courses off US/Pacific.

Three things a person reading the settings page could not have got right.

A switch called "Prevent sessions from automatically starting" makes the
reader work out that leaving it alone is what they want. Both flags are now
named for what they do and default to on, so the stored values have to flip.

And British Columbia stopped changing its clocks on 8 March 2026. US/Pacific
still turns back on 1 November, so from that date a course on it computes
every lecture an hour late -- which for a term running to December is most of
the second half. Courses left on the old default move to America/Vancouver; a
course deliberately set to something else is left alone.
"""

from django.db import migrations, models

import pytz

from peer_course.models import timezone_choices


def flip_and_relocate(apps, schema_editor):
    Course = apps.get_model("peer_course", "Course")
    for course in Course.objects.all():
        fields = ["auto_start", "export_prompt_at_end"]
        course.auto_start = not course.auto_start
        course.export_prompt_at_end = not course.export_prompt_at_end
        if course.course_timezone in ("US/Pacific", "America/Los_Angeles"):
            course.course_timezone = "America/Vancouver"
            fields.append("course_timezone")
        course.save(update_fields=fields)


def unflip(apps, schema_editor):
    Course = apps.get_model("peer_course", "Course")
    for course in Course.objects.all():
        course.auto_start = not course.auto_start
        course.export_prompt_at_end = not course.export_prompt_at_end
        course.save(update_fields=["auto_start", "export_prompt_at_end"])


class Migration(migrations.Migration):

    dependencies = [("peer_course", "0085_one_participation_system")]

    operations = [
        migrations.RenameField(
            model_name="course", old_name="auto_start_disabled",
            new_name="auto_start",
        ),
        migrations.RenameField(
            model_name="course", old_name="email_end_lecture_popup",
            new_name="export_prompt_at_end",
        ),
        # The rename carries the stored values across unchanged; this turns
        # "prevented" into "starts automatically".
        migrations.RunPython(flip_and_relocate, unflip),
        migrations.AlterField(
            model_name="course", name="auto_start",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Open a lecture at its scheduled time without anyone "
                    "pressing anything. Turn this off to start every lecture "
                    "by hand."
                ),
                verbose_name="Start lectures automatically",
            ),
        ),
        migrations.AlterField(
            model_name="course", name="export_prompt_at_end",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Open the download and email pop-up as soon as a lecture "
                    "is over."
                ),
                verbose_name="Offer the export when a lecture ends",
            ),
        ),
        migrations.AlterField(
            model_name="course", name="on_list",
            field=models.BooleanField(
                db_column="on_list", db_index=True, default=True,
                editable=False,
                verbose_name="Is listed as a Regular Courses",
            ),
        ),
        migrations.AlterField(
            model_name="course", name="course_timezone",
            field=models.CharField(
                choices=timezone_choices, default="America/Vancouver",
                help_text=(
                    "Every scheduled time on this course is read in this zone. "
                    "British Columbia keeps UTC-7 all year from March 2026, "
                    "which US/Pacific does not — on that zone every lecture "
                    "after 1 November would open an hour late."
                ),
                max_length=32, verbose_name="Course Timezone",
            ),
        ),
    ]
