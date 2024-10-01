from django.core.exceptions import PermissionDenied
import uuid

import csv, io


from .models import *

# Create your views here.


class CourseBase:
    @staticmethod
    def create(user, name, browsable, archived):
        "Create the course"

        c = Course(displayname=name, browsable=browsable, archived=archived)
        c.stucode = str(uuid.uuid3(uuid.NAMESPACE_DNS, name + "/STU"))[0:8]
        c.tascode = str(uuid.uuid3(uuid.NAMESPACE_DNS, name + "/TAS"))[0:8]
        c.instructor_code = str(uuid.uuid3(uuid.NAMESPACE_DNS, name + "/INS"))[0:8]
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
        return CourseBase.get_user_role(user, cid) == "instructor"

    @staticmethod
    def is_instructor_some_course(user, superuser_fine=True):
        if user.is_superuser and superuser_fine:
            return True
        return CourseMember._default_manager.filter(
            user=user, role="instructor"
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
        return cm.role in ['instructor', 'ta']


    @staticmethod
    def is_independent(user, cid):

        if user.is_superuser:
            return False

        cm = CourseBase.get_course_member(user, cid)
        return cm.is_independent

    @staticmethod
    def get_course_member(user, cid):
        return CourseMember._default_manager.filter(
            course__id=cid, user=user, active=True
        ).first()

    @staticmethod
    def get_graders(cid):
        return CourseBase.get_tas(cid)

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
        cm = CourseMember._default_manager.create(course=course, user=user, role=role, qualified=False)
        return cm

    @staticmethod
    def enroll(user, code):
        "Enroll a user in a course"

        c = Course._default_manager.filter(instructor_code=code).first()
        if c is not None:
            return CourseBase._enroll(user, c, "instructor")

        c = Course._default_manager.filter(browsable=True, stucode=code).first()
        if c is not None:
            return CourseBase._enroll(user, c, "student")

        c = Course._default_manager.filter(tascode=code).first()
        if c is not None:
            return CourseBase._enroll(user, c, "ta")

        return None

    @staticmethod
    def import_student_cis(csv_file, supervised_threshold, cid):
        count=0
        decoded_file = csv_file.read().decode('utf-8-sig')
        io_string = io.StringIO(decoded_file)
        for row in csv.reader(io_string, delimiter=',', quotechar='|'):
            stus= CourseMember._default_manager.filter(user__username=row[0], course__id=cid)
            stu=stus[0]
            stu.lower_confidence_bound= float(row[1])
            stu.markingload=  float(row[2])
            stu.upper_confidence_bound= float(row[3])
            if supervised_threshold is not None:                   
                if stu.lower_confidence_bound < supervised_threshold :
                    stu.is_independent= False
                else:
                    stu.is_independent= True
            stu.save()
            count=count+1

        return count


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
