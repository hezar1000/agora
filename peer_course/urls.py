from django.conf.urls import url
from django.urls import path
from django.conf import settings
from django.views.static import serve

from .views import CourseViews

urlpatterns = [
    url(r"^static/(?P<path>.*)$", serve, settings.STATIC_DICT),
    url(r"^list/$", CourseViews.list, name="list"),  # list all the courses
    # instructor actions
    url(r"^create/$", CourseViews.create),  # create a course
    url(
        r"^(?P<cid>[0-9]+)/$", CourseViews.view, name="view"
    ),  # view the details of a course
    url(r"^([0-9]+)/edit/$", CourseViews.edit),  # edit course configuraitons
    url(r"^([0-9]+)/modify/$", CourseViews.modify),  # show, hide, or archive a course
    url(r"^([0-9]+)/list_users/$", CourseViews.list_users),  # manage users of a course
    url(r"^([0-9]+)/add_user/$", CourseViews.add_user),  # add a user to a course
    url(r"^([0-9]+)/export_participation_data/$", CourseViews.export_participation_data),  
    url(r"^([0-9]+)/export_daily_participation_data/$", CourseViews.export_daily_participation_data), 
    url(
        r"^([0-9]+)/remove_user/([0-9]+)/$", CourseViews.remove_user
    ),  # remove a user from a course.
    url(
        r"^([0-9]+)/([0-9]+)/$", CourseViews.user_view
    ),  # view a specific user in a course
    url(r"^enroll/$", CourseViews.enroll),
    url(r"^([0-9]+)/import_ci/$", CourseViews.import_ci, name="import_ci"),  # import student's confidence interval
    url(r"^([0-9]+)/export_ci/$", CourseViews.export_ci, name="export_ci"),  # export student's confidence interval
    url(r"^([0-9]+)/export_instructors/$", CourseViews.export_instructors, name="export_instructors"),  # export student's confidence interval
    url(r"^([0-9]+)/export_tas/$", CourseViews.export_tas, name="export_tas"),  # export student's confidence interval
    url(r"^([0-9]+)/export_students/$", CourseViews.export_students, name="export_students"),  # export student's confidence interval

    url(r"^([0-9]+)/export_poll_participation_data/$", CourseViews.export_poll_participation),
    url(r"^([0-9]+)/export_poll_participation_data/(\d+)/$", CourseViews.export_poll_participation),
    url(r"^([0-9]+)/export_message_data/$", CourseViews.export_messages),
    url(r"^([0-9]+)/export_message_data/(\d+)/$", CourseViews.export_messages),
]
