from django.urls import path

from .views import *

urlpatterns = [
    path('student/', MessageViews.student, name='message_student'),
    path('instructor/', MessageViews.instructor, name='message_instructor'),
]