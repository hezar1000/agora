from django.urls import re_path

from . import attendance_views
from .sandbox_views import SandboxViews
from .views import CourseViews

urlpatterns = [
    re_path(r"^list/$", CourseViews.list, name="list"),
    re_path(r"^create/$", CourseViews.create, name="create"),  
    re_path(r"^(?P<cid>[0-9]+)/$", CourseViews.view, name="view"),  
    re_path(r"^(?P<cid>[0-9]+)/edit/$", CourseViews.edit, name="edit"),  
    re_path(r"^([0-9]+)/modify/$", CourseViews.modify),  
    re_path(r"^([0-9]+)/list_users/$", CourseViews.list_users),  
    re_path(r"^([0-9]+)/add_user/$", CourseViews.add_user),  
    re_path(r"^([0-9]+)/export_participation_data_custom/$",CourseViews.export_participation_data_custom,name="export_participation_data_custom",),
    re_path(r"^([0-9]+)/update_session_date/$", CourseViews.update_session_date, name="update_session_date"),
    re_path(r"^([0-9]+)/cancel_next_session/$", CourseViews.cancel_next_session, name="cancel_next_session"),
    re_path(r"^([0-9]+)/uncancel_next_session/$", CourseViews.uncancel_next_session, name="uncancel_next_session"),
    re_path(r"^([0-9]+)/update_session_cancel/$", CourseViews.update_session_cancel, name="update_session_cancel"),
    re_path(r"^([0-9]+)/delete_session/$", CourseViews.delete_session, name="delete_session"),
    re_path(r"^([0-9]+)/remove_user/([0-9]+)/$", CourseViews.remove_user),  
    re_path(r"^([0-9]+)/([0-9]+)/$", CourseViews.user_view),  
    re_path(r"^enroll/$", CourseViews.enroll, name="enroll"),  
    # Demonstration classes. `join` is the only one a signed-out person can
    # reach, and it does nothing without a live six-digit code.
    re_path(r"^demo/$", SandboxViews.start, name="sandbox_start"),
    re_path(r"^demo/join/$", SandboxViews.join, name="sandbox_join"),
    re_path(r"^demo/(?P<cid>[0-9]+)/$", SandboxViews.console, name="sandbox_console"),
    re_path(r"^demo/(?P<cid>[0-9]+)/end/$", SandboxViews.end, name="sandbox_end"),
    re_path(r"^demo/(?P<cid>[0-9]+)/microphone/$", SandboxViews.microphone, name="sandbox_microphone"),
    re_path(r"^([0-9]+)/set_lecture_skipped/$", CourseViews.set_lecture_skipped, name="set_lecture_skipped"),
    re_path(r"^([0-9]+)/export_instructors/$", CourseViews.export_instructors, name="export_instructors"),  
    re_path(r"^([0-9]+)/export_tas/$", CourseViews.export_tas, name="export_tas"),  
    re_path(r"^([0-9]+)/export_students/$", CourseViews.export_students, name="export_students"),  
    re_path(r"^([0-9]+)/export_participation_data_email/$", CourseViews.export_participation_data_email, name="export_participation_data_email"),  
    # These take their poll and date from the query string. A second path
    # segment used to be routed here too, which passed an argument neither view
    # accepts: every such URL was a 500, for instructors and students alike.
    re_path(r"^([0-9]+)/export_poll_participation_data/$", CourseViews.export_poll_participation),
    re_path(r"^([0-9]+)/export_message_data/$", CourseViews.export_messages),
    re_path( r"^([0-9]+)/get_color_config/$", CourseViews.get_color_config, name="get_color_config"),
    re_path(r"^([0-9]+)/rename_color/$",CourseViews.rename_color,name="rename_color"),
    re_path(r"^(?P<cid>[0-9]+)/hide_list/$", CourseViews.hide_list, name="hide_list"),
    # Attendance mode, driven from the lecture screen, and the page each
    # lecture keeps afterwards.
    re_path(r"^(?P<cid>[0-9]+)/attendance/$", attendance_views.state, name="attendance_state"),
    re_path(r"^(?P<cid>[0-9]+)/attendance/start/$", attendance_views.start, name="attendance_start"),
    re_path(r"^(?P<cid>[0-9]+)/attendance/announce/$", attendance_views.announce, name="attendance_announce"),
    re_path(r"^(?P<cid>[0-9]+)/attendance/close/$", attendance_views.close, name="attendance_close"),
    re_path(r"^(?P<cid>[0-9]+)/attendance/record/(?P<rid>[0-9]+)/$", attendance_views.review, name="attendance_review"),
    re_path(r"^(?P<cid>[0-9]+)/attendance/record/(?P<rid>[0-9]+)/remove/$", attendance_views.remove_added, name="attendance_remove_added"),
    re_path(r"^(?P<cid>[0-9]+)/attendance/(?P<check_id>[0-9]+)/add/$", attendance_views.add_present, name="attendance_add_present"),
    re_path(r"^(?P<cid>[0-9]+)/lectures/$", attendance_views.lectures, name="lectures"),
    re_path(r"^(?P<cid>[0-9]+)/lectures/(?P<sid>[0-9]+)/$", attendance_views.lecture, name="lecture"),
    re_path(r"^(?P<cid>[0-9]+)/lectures/(?P<sid>[0-9]+)/attendance\.csv$", attendance_views.attendance_csv, name="lecture_attendance_csv"),
    ]
