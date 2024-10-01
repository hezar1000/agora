from django import template

register = template.Library()

from django.contrib.auth.models import User

from peer_course.base import CourseBase


@register.inclusion_tag("user-list-for-course.html")
def list_users(course, user):
    "View all the users in the course"

    groups = dict()
    groups["instructor"] = [
        m.user for m in course.members.filter(role="instructor", active=True)
    ]
    groups["ta"] = [m.user for m in course.members.filter(role="ta", active=True)]
    groups["student"] = [
        m.user for m in course.members.filter(role="student", active=True)
    ]

    render_dict = dict()
    render_dict["course"] = course
    render_dict["groups"] = groups
    render_dict["is_instructor"] = CourseBase.is_instructor(user, course.id)
    render_dict["request_user"] = user

    # render_dict['users'] = User._default_manager.exclude(id__in=course.members.all())

    return render_dict
