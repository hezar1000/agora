from django.urls import path

from .views import *

urlpatterns = [
    path('instructor/', PollViews.instructor, name='polling_instructor'),
    path('student/', PollViews.student, name='polling_student')
]