# Generated by Django 2.1.15 on 2024-10-01 16:44

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('peer_course', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Lecture',
            fields=[
                ('lecture_id', models.AutoField(primary_key=True, serialize=False)),
                ('start_time', models.DateTimeField(auto_now_add=True)),
                ('end_time', models.DateTimeField(null=True)),
                ('enable_messages', models.BooleanField(default=True)),
                ('blocked_students', models.ManyToManyField(related_name='blocked_students', to=settings.AUTH_USER_MODEL)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='peer_course.Course')),
            ],
            options={
                'db_table': 'peer_lecture',
            },
        ),
        migrations.CreateModel(
            name='Message',
            fields=[
                ('message_id', models.AutoField(primary_key=True, serialize=False)),
                ('message', models.TextField()),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('reply_message', models.TextField(null=True)),
                ('blocked', models.BooleanField(default=False)),
                ('hidden', models.BooleanField(default=False)),
                ('broadcast', models.BooleanField(default=False)),
                ('auth_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('lecture', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='peer_lecture.Lecture')),
            ],
        ),
        migrations.CreateModel(
            name='Poll',
            fields=[
                ('poll_id', models.AutoField(primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('start_time', models.DateTimeField(null=True)),
                ('end_time', models.DateTimeField(null=True)),
                ('poll_data', models.TextField(null=True)),
                ('answer', models.CharField(max_length=200)),
                ('answer_options', models.TextField(null=True)),
                ('dont_save_answer', models.BooleanField()),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='peer_course.Course')),
                ('duplicate_of', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='peer_lecture.Poll')),
                ('lecture', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='peer_lecture.Lecture')),
            ],
        ),
        migrations.CreateModel(
            name='PollResult',
            fields=[
                ('result_id', models.AutoField(primary_key=True, serialize=False)),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('answer', models.CharField(max_length=200, null=True)),
                ('auth_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('poll', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='peer_lecture.Poll')),
            ],
        ),
    ]
