from django.conf.urls import url, include

from .views import HomeViews
from peer_lecture import views

urlpatterns = [
    url(r"^$", HomeViews.render, name="home"),
    url(r"^polling/", include('peer_lecture.urls_polling')),
    url(r"^message/", include('peer_lecture.urls_message')),
    url(
        r"^update_course/$", HomeViews.update_course
    ),  # display all assignments in all courses
    url(r"^random_student/$", HomeViews.random_student, name="random_student"),
    url(r"^random_student_blue/$", HomeViews.random_student_blue, name="random_student_blue"),
    url(r"^random_student_red/$", HomeViews.random_student_red, name="random_student_red"),
    url(r"^random_student_yellow/$", HomeViews.random_student_yellow, name="random_student_yellow"),

    url(r"^clear_all/$", HomeViews.clear_all, name="clear_all"),
    url(r"^clear_all_blue/$", HomeViews.clear_all_blue, name="clear_all_blue"),
    url(r"^clear_all_red/$", HomeViews.clear_all_red, name="clear_all_red"),
    url(r"^clear_all_yellow/$", HomeViews.clear_all_yellow, name="clear_all_yellow"),
    url(r"^clear_all_lists/$", HomeViews.clear_all_lists, name="clear_all_lists"),


    url(r"^enable/$", HomeViews.enable, name="enable"),
    url(r"^enable_blue/$", HomeViews.enable_blue, name="enable_blue"),
    url(r"^enable_red/$", HomeViews.enable_red, name="enable_red"),
    url(r"^enable_yellow/$", HomeViews.enable_yellow, name="enable_yellow"),
    url(r"^enable_all/$", HomeViews.enable_all, name="enable_all"),


    url(r"^disable/$", HomeViews.disable, name="disable"),
    url(r"^disable_blue/$", HomeViews.disable_blue, name="disable_blue"),
    url(r"^disable_red/$", HomeViews.disable_red, name="disable_red"),
    url(r"^disable_yellow/$", HomeViews.disable_yellow, name="disable_yellow"),
    url(r"^disable_all/$", HomeViews.disable_all, name="disable_all"),


    url(r"^check_status/$", HomeViews.check_status, name="check_status"),
    url(r"^count_hands_up/$", HomeViews.count_hands_up, name="count_hands_up"),
    url(r"^count_already_spoken/$", HomeViews.count_already_spoken, name="count_already_spoken"),
    url(r"^disqualify_all/$", HomeViews.disqualify_all, name="disqualify_all"),
    url(r"^reset_class_participation/$", HomeViews.reset_class_participation, name="reset_class_participation"),
    url(r"^reset_participation_points/$", HomeViews.reset_participation_points, name="reset_participation_points"),
    url(r"^choose_next/([0-9]+)/([0-9]+)$", HomeViews.choose_next), 
    url(r"^undo/$", HomeViews.undo, name="undo"),
    url(r"^start_timer/$", HomeViews.start_timer, name="start_timer"),
    url(r"^stop_timer/$", HomeViews.stop_timer, name="stop_timer"),
]




