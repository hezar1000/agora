from django.core.exceptions import PermissionDenied
import secrets
from datetime import timedelta

from django.utils import timezone
from django.db.models import Q


from . import passphrase
from .models import *
from .models import INSTRUCTOR_ROLES, STAFF_ROLES

# Create your views here.


class CourseBase:

    @staticmethod
    def _secure_code(nbytes=18):
        return secrets.token_urlsafe(nbytes)

    @staticmethod
    def _student_code():
        """A code a class can be told out loud; see peer_course.passphrase."""
        return passphrase.generate()

    @staticmethod
    def generate_unique_code(nbytes=18, max_attempts=100, make=None):
        """A code no other course is using.

        `make` builds each candidate and defaults to a random token, which is
        right for the TA and instructor codes: those are pasted into an email
        once, and whoever types the instructor code becomes an instructor. The
        student code is made of words instead, because a room full of people
        has to be able to type it off a slide.
        """
        make = make or (lambda: CourseBase._secure_code(nbytes))
        for _ in range(max_attempts):
            code = make()
            if not Course.objects.filter(
                Q(stucode=code)
                | Q(tascode=code)
                | Q(instructor_code=code)
            ).exists():
                return code
        raise RuntimeError("Unable to generate a unique enrollment capability")

    @staticmethod
    def create(user, name, browsable, agora_mode = True, archived = False, on_list = False):
        "Create the course"
        if not name or not name.strip():
            raise ValueError("Course name cannot be empty")
        c = Course(displayname=name, browsable=browsable, archived=archived, agora_mode=agora_mode, on_list=on_list)
        expires_at = timezone.now() + timedelta(days=180)
        c.stucode = CourseBase.generate_unique_code(make=CourseBase._student_code)
        c.tascode = CourseBase.generate_unique_code()
        c.instructor_code = CourseBase.generate_unique_code()
        c.student_code_expires_at = expires_at
        c.ta_code_expires_at = expires_at
        c.instructor_code_expires_at = expires_at
        c.save()

        return c

    @staticmethod
    def get_user_role(user, cid):
        cm = CourseBase.get_course_member(user, cid)
        if cm:
            return cm.role
        return None

    @staticmethod
    def is_student(user, cid, superuser_fine=True):
        if user.is_superuser and superuser_fine:
            return False
        return CourseBase.get_user_role(user, cid) == "student"

    @staticmethod
    def is_course_member(user, cid, superuser_fine=True):
        if user.is_superuser and superuser_fine:
            return True
        return CourseBase.get_user_role(user, cid) is not None

    @staticmethod
    def is_ta(user, cid, superuser_fine=True):
        if user.is_superuser and superuser_fine:
            return False
        return CourseBase.get_user_role(user, cid) == "ta"

    @staticmethod
    def is_instructor(user, cid, superuser_fine=True):
        if user.is_superuser and superuser_fine:
            return True
        return CourseBase.get_user_role(user, cid) in INSTRUCTOR_ROLES

    @staticmethod
    def is_instructor_some_course(user, superuser_fine=True):
        if user.is_superuser and superuser_fine:
            return True
        return CourseMember._default_manager.filter(
            user=user, role__in=INSTRUCTOR_ROLES, active=True
        ).exists()

    @staticmethod
    def is_course_staff(user, cid, superuser_fine=True):
        return CourseBase.is_instructor(user, cid, superuser_fine) or CourseBase.is_ta(
            user, cid, superuser_fine
        )

    @staticmethod
    def is_cm_staff(cm, user, superuser_fine=True):
        if superuser_fine and user.is_superuser:
            return True
        if cm is None:
            return False
        return cm.role in STAFF_ROLES


    @staticmethod
    def get_course_member(user, cid):
        coursemember = CourseMember._default_manager.filter(
            course__id=cid, user=user, active=True
        ).first()
        if coursemember is None and user.is_superuser:
            course = Course._default_manager.get(id=cid)
            coursemember = CourseMember(
                course=course,
                user=user,
                role='superuser',  
                active=True,
            )
        return coursemember

    @staticmethod
    def get_course_students(cid):
        return CourseMember._default_manager.filter(
            course__id=cid, role="student", active=True
        )

    @staticmethod
    def get_course_staff(cid):
        return CourseMember._default_manager.filter(
            course__id=cid, role__in=["instructor", "ta"], active=True
        )

    @staticmethod
    def get_tas(cid):
        return CourseMember._default_manager.filter(
            course__id=cid, role="ta", active=True
        )

    @staticmethod
    def get_students(cid):
        return CourseMember._default_manager.filter(
            course__id=cid, role="student", active=True
        )

    @staticmethod
    def get_courses(user):
        return [cm.course for cm in user.memberships.filter(active=True)]

    @staticmethod
    def _enroll(user, course, role):
        "Enroll a user in a course with a given role"
        existing_cm = CourseMember._default_manager.filter(
            course=course, user=user
        ).first()
        if existing_cm is not None:
            if existing_cm.active:
                raise AssertionError(
                    "Already enrolled in this course as: "
                    + existing_cm.role.capitalize()
                )
            else:
                raise AssertionError(
                    "Your membership in this course has been deactivated, please contact course staff"
                )
        cm = CourseMember._default_manager.create(course=course, user=user, role=role)
        return cm

    @staticmethod
    def enroll(user, code):
        "Enroll a user in a course"

        now = timezone.now()
        c = Course._default_manager.filter(
            instructor_code=code,
            instructor_code_expires_at__gt=now,
        ).first()
        if c is not None:
            return CourseBase._enroll(user, c, "instructor")

        c = Course._default_manager.filter(
            browsable=True,
            stucode=code,
            student_code_expires_at__gt=now,
        ).first()
        if c is not None:
            return CourseBase._enroll(user, c, "student")

        c = Course._default_manager.filter(
            tascode=code,
            ta_code_expires_at__gt=now,
        ).first()
        if c is not None:
            return CourseBase._enroll(user, c, "ta")

        return None

class CoursePermissions:
    @staticmethod
    def require_course_staff(user, cid, message=None, superuser_fine=True):
        if not CourseBase.is_course_staff(user, cid, superuser_fine):
            # TODO: fix the message maybe?
            raise PermissionDenied(
                "Access to this page is only allowed for course staff"
                if message is None
                else message
            )

    @staticmethod
    def require_instructor_some_course(user, message=None, superuser_fine=True):
        if not CourseBase.is_instructor_some_course(user, superuser_fine):
            raise PermissionDenied(
                "Access to this page is only allowed for course instructor(s)"
                if message is None
                else message
            )

    @staticmethod
    def require_instructor(user, cid, message=None, superuser_fine=True):
        if not CourseBase.is_instructor(user, cid, superuser_fine):
            raise PermissionDenied(
                "Access to this page is only allowed for course instructor(s)"
                if message is None
                else message
            )

    @staticmethod
    def require_ta(user, cid, message=None, superuser_fine=True):
        if not CourseBase.is_ta(user, cid, superuser_fine):
            raise PermissionDenied(
                "Access to this page is only allowed for course TA(s)"
                if message is None
                else message
            )

    @staticmethod
    def require_course_member(user, cid, message=None, superuser_fine=True):
        if not CourseBase.is_course_member(user, cid, superuser_fine):
            raise PermissionDenied(
                "Access to this page is only allowed for course member(s)"
                if message is None
                else message
            )
