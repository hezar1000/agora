from django.db import migrations, models


def merge_duplicate_real_sessions(apps, schema_editor):
    CourseSession = apps.get_model("peer_course", "CourseSession")
    CourseParticipation = apps.get_model("peer_course", "CourseParticipation")

    duplicates = (
        CourseSession.objects.filter(is_template=False)
        .exclude(session_date=None)
        .exclude(start_time_pst=None)
        .exclude(end_time_pst=None)
        .values("course_id", "session_date", "start_time_pst", "end_time_pst")
        .annotate(row_count=models.Count("id"))
        .filter(row_count__gt=1)
    )

    for duplicate in duplicates.iterator():
        filters = {
            key: duplicate[key]
            for key in ("course_id", "session_date", "start_time_pst", "end_time_pst")
        }
        rows = list(
            CourseSession.objects.filter(is_template=False, **filters).order_by("id")
        )
        keeper, redundant = rows[0], rows[1:]
        redundant_ids = [row.id for row in redundant]
        CourseParticipation.objects.filter(session_id__in=redundant_ids).update(
            session_id=keeper.id
        )
        CourseSession.objects.filter(id__in=redundant_ids).delete()


class Migration(migrations.Migration):
    dependencies = [("peer_course", "0071_secure_enrollment_codes")]

    operations = [
        migrations.RunPython(merge_duplicate_real_sessions, migrations.RunPython.noop),
        migrations.CreateModel(
            name="SchedulerLease",
            fields=[
                ("name", models.CharField(max_length=100, primary_key=True, serialize=False)),
                ("owner", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "scheduler_lease"},
        ),
        migrations.AddConstraint(
            model_name="coursesession",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_template", False)),
                fields=("course", "session_date", "start_time_pst", "end_time_pst"),
                name="unique_agora_real_session_slot",
            ),
        ),
    ]
