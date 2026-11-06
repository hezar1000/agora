from django.urls import include, re_path
from .views import HomeViews
from peer_lecture import views

urlpatterns = [
    re_path(r"^$", HomeViews.render, name="home"),
    re_path(r"^polling/", include('peer_lecture.urls_polling')),
    re_path(r"^message/", include('peer_lecture.urls_message')),
    re_path(r"^agora_dash/$", HomeViews.agora_dash, name="agora_dash"),
    re_path(r"^sound/(?P<course_id>[0-9]+)/$", HomeViews.sound_only, name="sound_only"),
    re_path(r"^polls/$", HomeViews.polls, name="polls"),
    re_path(r"^random_student/$", HomeViews.random_student, name="random_student"),
    re_path(r"^random_student_blue/$", HomeViews.random_student_blue, name="random_student_blue"),
    re_path(r"^random_student_red/$", HomeViews.random_student_red, name="random_student_red"),
    re_path(r"^random_student_yellow/$", HomeViews.random_student_yellow, name="random_student_yellow"),

    re_path(r"^clear_all/$", HomeViews.clear_all, name="clear_all"),
    re_path(r"^clear_all_blue/$", HomeViews.clear_all_blue, name="clear_all_blue"),
    re_path(r"^clear_all_red/$", HomeViews.clear_all_red, name="clear_all_red"),
    re_path(r"^clear_all_yellow/$", HomeViews.clear_all_yellow, name="clear_all_yellow"),
    re_path(r"^clear_all_lists/$", HomeViews.clear_all_lists, name="clear_all_lists"),


    re_path(r"^enable/$", HomeViews.enable, name="enable"),
    re_path(r"^enable_blue/$", HomeViews.enable_blue, name="enable_blue"),
    re_path(r"^enable_red/$", HomeViews.enable_red, name="enable_red"),
    re_path(r"^enable_yellow/$", HomeViews.enable_yellow, name="enable_yellow"),
    re_path(r"^enable_all/$", HomeViews.enable_all, name="enable_all"),


    re_path(r"^disable/$", HomeViews.disable, name="disable"),
    re_path(r"^disable_blue/$", HomeViews.disable_blue, name="disable_blue"),
    re_path(r"^disable_red/$", HomeViews.disable_red, name="disable_red"),
    re_path(r"^disable_yellow/$", HomeViews.disable_yellow, name="disable_yellow"),
    re_path(r"^disable_all/$", HomeViews.disable_all, name="disable_all"),


    re_path(r"^check_status/$", HomeViews.check_status, name="check_status"),
    re_path(r"^count_hands_up/$", HomeViews.count_hands_up, name="count_hands_up"),
    re_path(r"^count_already_spoken/$", HomeViews.count_already_spoken, name="count_already_spoken"),
    re_path(r"^reset_class_participation/$", HomeViews.reset_class_participation, name="reset_class_participation"),
    re_path(r"^reset_participation_points/$", HomeViews.reset_participation_points, name="reset_participation_points"),
    re_path(r"^choose_next/([0-9]+)/([0-9]+)/$", HomeViews.choose_next), 
    re_path(r"^undo/$", HomeViews.undo, name="undo"),
]




