import secrets
from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def rotate_codes(apps, schema_editor):
    Course = apps.get_model("peer_course", "Course")
    expires_at = timezone.now() + timedelta(days=180)
    used = set()

    def token():
        while True:
            value = secrets.token_urlsafe(18)
            if value not in used:
                used.add(value)
                return value

    for course in Course.objects.all().iterator():
        course.stucode = token()
        course.tascode = token()
        course.instructor_code = token()
        course.student_code_expires_at = expires_at
        course.ta_code_expires_at = expires_at
        course.instructor_code_expires_at = expires_at
        course.save(
            update_fields=[
                "stucode",
                "tascode",
                "instructor_code",
                "student_code_expires_at",
                "ta_code_expires_at",
                "instructor_code_expires_at",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("peer_course", "0070_remove_course_essay_char_limit")]

    operations = [
        migrations.AddField(
            model_name="course",
            name="student_code_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="course",
            name="ta_code_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="course",
            name="instructor_code_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(rotate_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="course",
            name="stucode",
            field=models.CharField(
                db_column="student_enroll_code",
                max_length=128,
                null=True,
                unique=True,
                verbose_name="Student Enroll Code",
            ),
        ),
        migrations.AlterField(
            model_name="course",
            name="tascode",
            field=models.CharField(
                db_column="ta_enroll_code",
                max_length=128,
                null=True,
                unique=True,
                verbose_name="TA Enroll Code",
            ),
        ),
        migrations.AlterField(
            model_name="course",
            name="instructor_code",
            field=models.CharField(
                db_column="instructor_enroll_code",
                max_length=128,
                null=True,
                unique=True,
                verbose_name="Instructor Enroll Code",
            ),
        ),
    ]
