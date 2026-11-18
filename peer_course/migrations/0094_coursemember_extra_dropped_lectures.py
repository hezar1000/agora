# Lectures dropped for one student on top of the course's own drops.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('peer_course', '0093_remove_mta_columns'),
    ]

    operations = [
        migrations.AddField(
            model_name='coursemember',
            name='extra_dropped_lectures',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
