from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('peer_lecture', '0033_lecture_guest_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='classroommicrophone',
            name='relay_preferred',
            field=models.BooleanField(default=False),
        ),
    ]
