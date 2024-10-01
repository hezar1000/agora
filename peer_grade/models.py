from django.db import models

from peer_course.models import CourseMember
from .choices import APPEAL_STATUS_CHOICES, CLOSED, RESOLVED, INPROGRESS

from django.core.validators import *




class GradingItem(models.Model):
    gradee = models.ForeignKey(
        CourseMember, related_name="gradee", on_delete=models.CASCADE
    )

    grade_type = models.TextField(blank=True, null=True, default="[unspecified]") # assignment or peer review or participation, etc.?

    grading_period = models.TextField(blank=True, null=True, default="[unspecified]") # which week?

    grade = models.FloatField(default=0)

    max_grade = models.FloatField(default=0)

    grading_method = models.TextField(blank=True, null=True, default="[ungraded]") # TA or Peer?

    comments = models.TextField(blank=True, null=True, default=None) # aditional comments



