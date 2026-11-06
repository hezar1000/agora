from django.db import migrations, models

import peer_course.models


class Migration(migrations.Migration):
    dependencies = [("peer_course", "0072_scheduler_lease_and_session_uniqueness")]

    operations = [
        migrations.AlterField(
            model_name="course",
            name="course_timezone",
            field=models.CharField(
                choices=peer_course.models.timezone_choices,
                default="US/Pacific",
                max_length=32,
                verbose_name="Course Timezone",
            ),
        )
    ]
