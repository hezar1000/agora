from django_cron import CronJobBase, Schedule
from datetime import datetime, timedelta

from .models import Lecture

class AutoEndLectureCronJob(CronJobBase):
    RUN_EVERY_MINS = 60

    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'peer_lecture.cron.auto_end_lecture'

    def do(self):
        lectures = Lecture.objects.filter(end_time__isnull=True)

        for lecture in lectures:
            start_time = lecture.start_time.replace(tzinfo=None)
            current_time = datetime.now().astimezone(lecture.start_time.tzinfo).replace(tzinfo=None)

            if current_time >= start_time + timedelta(hours=6):
                lecture.end_time = current_time.astimezone(lecture.start_time.tzinfo)
                lecture.save()