from django import forms
from django.forms import DateTimeField
from django.contrib.admin import widgets
from django.utils.safestring import mark_safe
from django.core.exceptions import ValidationError
from django.utils import timezone

import re, datetime

from peer_course.base import CourseBase
from peer_home.forms import ModelFormControl




class ImportStudentGrades(forms.Form):
    file = forms.FileField()


class UploadComponentGrades(forms.Form):
    file = forms.FileField()


class UploadGradingItems(forms.Form):
    file = forms.FileField()

