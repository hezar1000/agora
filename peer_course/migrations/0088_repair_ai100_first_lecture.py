from datetime import date, datetime, time, timezone

from django.db import migrations


def repair_ai100(apps, schema_editor):
    """Expose the configured grade and repair the first lecture's close.

    AI 100 entered all seventeen sessions before term.  The old lifecycle
    cleanup consequently marked Lecture 17 ended when Lecture 1 closed.  The
    exact affected rows are known from the production audit; keep this repair
    deliberately course- and date-specific.
    """
    using = schema_editor.connection.alias
    Course = apps.get_model("peer_course", "Course")
    CourseMember = apps.get_model("peer_course", "CourseMember")
    CourseSession = apps.get_model("peer_course", "CourseSession")
    HandRaise = apps.get_model("peer_course", "HandRaise")
    Lecture = apps.get_model("peer_lecture", "Lecture")

    course = Course.objects.using(using).filter(
        displayname="AI 100 2026W1"
    ).first()
    if course is None:
        return

    Course.objects.using(using).filter(pk=course.pk).update(
        show_participation_grade=True
    )

    first = CourseSession.objects.using(using).filter(
        course_id=course.pk,
        is_template=False,
        session_date=date(2026, 9, 9),
    ).first()
    final = CourseSession.objects.using(using).filter(
        course_id=course.pk,
        is_template=False,
        session_date=date(2026, 12, 2),
    ).first()
    if first is None or final is None or not final.ended or first.ended:
        return

    correct_end = datetime(2026, 9, 9, 22, 30, tzinfo=timezone.utc)
    CourseSession.objects.using(using).filter(pk=first.pk).update(
        ended=True,
        actual_end_time=time(15, 30),
    )
    CourseSession.objects.using(using).filter(pk=final.pk).update(
        ended=False,
        actual_end_time=None,
    )
    Lecture.objects.using(using).filter(
        course_id=course.pk,
        start_time__date=date(2026, 9, 9),
        end_time__date=date(2026, 12, 2),
    ).update(end_time=correct_end)

    open_raises = list(
        HandRaise.objects.using(using).filter(
            session_id=first.pk,
            resolved_at__isnull=True,
        ).values_list("member_id", "participation_list")
    )
    HandRaise.objects.using(using).filter(
        session_id=first.pk,
        resolved_at__isnull=True,
    ).update(resolved_at=correct_end, resolution="cleared")
    fields = {
        1: "hand_up",
        2: "hand_up_list_2",
        3: "hand_up_list_3",
        4: "hand_up_list_4",
    }
    for member_id, participation_list in open_raises:
        field = fields.get(participation_list)
        if field:
            CourseMember.objects.using(using).filter(pk=member_id).update(
                **{field: False}
            )


class Migration(migrations.Migration):
    dependencies = [
        ("peer_course", "0087_course_is_sandbox_course_join_code_and_more"),
        ("peer_lecture", "0030_secure_poll_history_and_results"),
    ]

    operations = [
        migrations.RunPython(repair_ai100, migrations.RunPython.noop),
    ]
