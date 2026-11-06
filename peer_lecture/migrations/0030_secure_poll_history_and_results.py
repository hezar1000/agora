from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def close_duplicate_live_rows(apps, schema_editor):
    Lecture = apps.get_model("peer_lecture", "Lecture")
    Poll = apps.get_model("peer_lecture", "Poll")

    duplicate_lectures = (
        Lecture.objects.filter(end_time__isnull=True)
        .values("course_id")
        .annotate(row_count=models.Count("lecture_id"))
        .filter(row_count__gt=1)
    )
    for duplicate in duplicate_lectures.iterator():
        live = Lecture.objects.filter(
            course_id=duplicate["course_id"],
            end_time__isnull=True,
        ).order_by("-start_time", "-lecture_id")
        keep = live.first()
        closed_at = keep.start_time or timezone.now()
        stale_ids = list(live.exclude(lecture_id=keep.lecture_id).values_list("lecture_id", flat=True))
        Lecture.objects.filter(lecture_id__in=stale_ids).update(end_time=closed_at)
        Poll.objects.filter(lecture_id__in=stale_ids, end_time__isnull=True).update(end_time=closed_at)

    duplicate_polls = (
        Poll.objects.filter(start_time__isnull=False, end_time__isnull=True)
        .exclude(lecture_id__isnull=True)
        .values("lecture_id")
        .annotate(row_count=models.Count("poll_id"))
        .filter(row_count__gt=1)
    )
    for duplicate in duplicate_polls.iterator():
        live = Poll.objects.filter(
            lecture_id=duplicate["lecture_id"],
            start_time__isnull=False,
            end_time__isnull=True,
        ).order_by("-start_time", "-poll_id")
        keep = live.first()
        stale_poll_ids = list(
            live.exclude(poll_id=keep.poll_id).values_list("poll_id", flat=True)
        )
        Poll.objects.filter(poll_id__in=stale_poll_ids).update(
            end_time=keep.start_time or timezone.now()
        )


def remove_duplicate_results(apps, schema_editor):
    PollResult = apps.get_model("peer_lecture", "PollResult")
    duplicates = (
        PollResult.objects.values("poll_id", "auth_user_id")
        .annotate(row_count=models.Count("result_id"))
        .filter(row_count__gt=1)
    )
    for duplicate in duplicates.iterator():
        rows = PollResult.objects.filter(
            poll_id=duplicate["poll_id"],
            auth_user_id=duplicate["auth_user_id"],
        ).order_by("-time", "-result_id")
        keep_id = rows.values_list("result_id", flat=True).first()
        rows.exclude(result_id=keep_id).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("peer_lecture", "0029_poll_max_word_count_poll_min_word_count_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="poll",
            name="archived",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="poll",
            name="hands_moved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="poll",
            name="duplicate_of",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                to="peer_lecture.poll",
            ),
        ),
        migrations.RunPython(close_duplicate_live_rows, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="lecture",
            constraint=models.UniqueConstraint(
                condition=models.Q(end_time__isnull=True),
                fields=("course",),
                name="unique_open_lecture_per_course",
            ),
        ),
        migrations.AddConstraint(
            model_name="poll",
            constraint=models.UniqueConstraint(
                condition=models.Q(end_time__isnull=True, start_time__isnull=False),
                fields=("lecture",),
                name="unique_open_poll_per_lecture",
            ),
        ),
        migrations.RunPython(remove_duplicate_results, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="pollresult",
            constraint=models.UniqueConstraint(
                fields=("poll", "auth_user"),
                name="unique_poll_result_per_user",
            ),
        ),
    ]
