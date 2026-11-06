from django.urls import path
from peer_lecture.views import PollViews

from .views import *

urlpatterns = [
    path('instructor/', PollViews.instructor, name='polling_instructor'),
    path('student/', PollViews.student, name='polling_student'),
    path('course/<int:cid>/poll-instances/<int:saved_poll_id>/', PollViews.get_all_instances, name='get_all_instances'),
    path('poll-results-json/<int:poll_id>/', PollViews.get_poll_results_json, name='get_poll_results_json'),
    path('upload/', PollViews.upload_all_polls_json, name='upload_all_polls_json'), # <-- ADD THIS LINE
    path('download/', PollViews.download_all_polls_json, name='download_all_polls_json'),
    path('poll-results-count-json/<int:poll_id>/', PollViews.get_poll_results_with_count_json, name='get_poll_results_with_count_json'),
    path("polling/student/course/<int:cid>/poll-instances/<int:saved_poll_id>/", PollViews.get_all_instances_student, name="get_all_instances_student"),
    path('student/my-answer-json/<int:poll_id>/', PollViews.get_my_answer_json, name='get_my_answer_json'),

]