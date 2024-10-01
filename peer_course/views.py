import logging
import datetime

import pandas as pd

from django.shortcuts import get_object_or_404, HttpResponseRedirect, HttpResponse
import csv

from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from django.db import models
from django.shortcuts import reverse, get_object_or_404

from django.shortcuts import render as djangoRender

from peer_home.wrappers import render

from peer_lecture.models import Lecture, Poll, PollResult, Message

from .models import *
from .forms import CourseForm, ImportStudentCIs
from .decorators import chosen_course_required
from .base import CourseBase, CoursePermissions
from itertools import groupby


eventLogger = logging.getLogger("agora.events")

# Create your views here.


class CourseViews:
    @staticmethod
    @login_required
    def create(request):
        "Create a course"

        assert request.method == "POST"

        cname = request.POST.get("cname")
        browsable = False  # default value
        archived = False  # default value

        if Course._default_manager.filter(displayname=cname).exists():
            messages.error(
                request,
                "Cannot create course with name %s because there is already a course with this name."
                % cname,
            )
            return HttpResponseRedirect("/course/list/")

        c = CourseBase.create(request.user, cname, browsable, archived)
        if c is not None:
            messages.success(request, "Successfully created the course %s" % cname)
            messages.warning(request, "A course is NOT browsable when created.")

            # Course is a django.db model object and I want to get instructor_code from it
            code = c.instructor_code

            CourseBase.enroll(request.user, code)
            return HttpResponseRedirect("/course/list/")
        else:
            messages.error(request, "Error occurred creating the course %s" % cname)

    @staticmethod
    @login_required
    def list(request):
        "View the list of courses that I am associated with"

        render_dict = dict()

        cid = request.session.get("course_id", None)
        if cid is not None:
            course = Course._default_manager.get(pk=request.session["course_id"])
            render_dict["course"] = course

        render_dict["courses"] = Course._default_manager.all().order_by("displayname")
        render_dict["messages"] = get_messages(request)

        courses_as_student = request.user.memberships.filter(
            role="student", active=True
        )
        courses_as_ta = request.user.memberships.filter(role="ta", active=True)
        courses_as_instructor = request.user.memberships.filter(
            role="instructor", active=True
        )

        render_dict["courses_as_student"] = courses_as_student
        render_dict["courses_as_ta"] = courses_as_ta
        render_dict["courses_as_instructor"] = courses_as_instructor
        render_dict["has_visible_course"] = (
            (request.user.is_superuser and Course._default_manager.exists())
            or courses_as_student.exists()
            or courses_as_ta.exists()
            or courses_as_instructor.exists()
        )

        render_dict["next"] = request.GET.get("next", "")

        return render(request, "course-list.html", render_dict)

    @staticmethod
    @login_required
    def edit(request, cid):
        "Edit the course configurations"
        render_dict = dict()
        course = Course._default_manager.get(id=cid)

        CoursePermissions.require_instructor(request.user, cid)

        if request.method == "POST":
            CoursePermissions.require_instructor(request.user, cid)
            form = CourseForm(request.POST, instance=course)
            if form.is_valid():
                form.save()
                return HttpResponseRedirect(reverse("home:home"))
            else:
                messages.error(request, form.errors)
                render_dict["formCourse"] = form
                render_dict["course"] = course
                render_dict["messages"] = get_messages(request)
                if course.enable_participation:
                    return render(request, "course-edit-participation.html", render_dict)
                return render(request, "course-edit.html", render_dict)
        else:
            course_form = CourseForm(instance=course)
            render_dict["formCourse"] = course_form
            render_dict["course"] = course
            if course.enable_participation:
                return render(request, "course-edit-participation.html", render_dict)
            return render(request, "course-edit.html", render_dict)

    @staticmethod
    @login_required
    def view(request, cid):
        course = Course._default_manager.get(id=cid)
        request.session["course_id"] = course.id

        return HttpResponseRedirect(reverse("home:home"))

    @staticmethod
    @login_required
    def modify(request, cid):
        "Show, hide, or archive a course"

        action = request.GET.get("action", "")

        CoursePermissions.require_instructor(request.user, cid)

        eventLogger.info("Modifying course (%s) with action: %s" % (str(cid), action))

        if action == "show":
            Course._default_manager.filter(id=cid).update(browsable=True)
        elif action == "hide":
            Course._default_manager.filter(id=cid).update(browsable=False)
        elif action == "archive":
            Course._default_manager.filter(id=cid).update(archived=True)
        #    elif action == "restore" :
        #        CourseBase.restore(cid)
        # We do not ever want to delete a course.
        # elif action == "delete" :
        #     render_dict["error"] = "Deleted."
        #     CourseBase.delete(cid)
        else:
            messages.error(request, "Operation not supported")
        return HttpResponseRedirect("/course/list/")

    @staticmethod
    @login_required
    def list_users(request, cid):
        "View all the users in the course"

        CoursePermissions.require_course_staff(request.user, cid)

        course = Course._default_manager.get(id=cid)
        render_dict = dict()
        render_dict["course"] = course
        if course.enable_participation :
            return render(request, "course-list-users-participation.html", render_dict)
        return render(request, "course-list-users.html", render_dict)

    @staticmethod
    @login_required
    def user_view(request, cid, uid):
        "View a specific user's detail in a course"

        CoursePermissions.require_course_staff(request.user, cid)

        course = Course._default_manager.get(id=cid)
        user = get_object_or_404(User, pk=uid)

        render_dict = dict()

        render_dict["course"] = course
        render_dict["cid"] = course.id
        render_dict["user"] = user


        coursemember = CourseBase.get_course_member(user, course.id)

        # print(render_dict['submissions'])


        render_dict["coursemember"] = coursemember

        render_dict["now"] = timezone.now()

    

     #   render_dict["is_instructor"] = CourseBase.is_instructor(user, cid)
     #   render_dict["is_ta"] = CourseBase.is_ta(user, cid)
        render_dict["is_a_student"] = CourseBase.is_student(user, cid)
        render_dict["is_a_ta"] = CourseBase.is_ta(user, cid)
        render_dict["is_independent"] = CourseBase.is_independent(user, cid)

        render_dict["date_changed"] = coursemember.time_is_independent_changed

        return render(request, "course-user-view.html", render_dict)

    @staticmethod
    @login_required
    def add_user(request, cid):
        "Add the selected user to the course for the role"

        CoursePermissions.require_instructor(request.user, cid)

        if request.method == "POST":

            course = get_object_or_404(Course, pk=cid)
            user_id = request.POST.get("user", 0)
            if user_id == "":
                user_id = 0
            user = get_object_or_404(User, pk=user_id)
            role = request.POST.get("role", "student")
            if role == "":
                role = "student"
            cm = CourseMember._default_manager.filter(course=course, user=user).first()

            eventLogger.info(
                "Adding user %s (%s) with the role `%s` to course %s (%s)"
                % (
                    user.username,
                    str(user.id),
                    role,
                    course.displayname,
                    str(course.id),
                )
            )

            if cm is not None:
                if cm.active:
                    messages.error(
                        request,
                        "Cannot add user %s to this course.  This user is already associated with this course."
                        % user.username,
                    )
                else:
                    cm.active = True
                    cm.role = role
                    cm.save()
            else:
                CourseMember._default_manager.create(
                    course=course, user=user, role=role
                )

        return HttpResponseRedirect("/course/%s/list_users/" % cid)

    @staticmethod
    @login_required
    def remove_user(request, cid, uid):
        "Deactivates user account: he/she can't activate it by themselves"

        CoursePermissions.require_instructor(request.user, cid)

        course = get_object_or_404(Course, pk=cid)
        user = get_object_or_404(User, pk=uid)

        eventLogger.warning(
            "Deactivating user %s (%s) from the course %s (%s)"
            % (user.username, str(user.id), course.displayname, str(course.id))
        )

        CourseMember._default_manager.filter(course=course, user=user).update(
            active=False
        )

        return HttpResponseRedirect("/course/%s/list_users/" % cid)

    @staticmethod
    @login_required
    def enroll(request):
        "Enroll the user in a course"

        if request.method == "POST":
            code = request.POST.get("coursecode")

            try:
                cm = CourseBase.enroll(request.user, code)

                if cm is not None:
                    messages.success(
                        request, "Successfully enrolled in course with code %s" % code
                    )

                    eventLogger.info(
                        "Enrolled user %s (%s) with the role `%s` in the course %s (%s)"
                        % (
                            cm.user.username,
                            str(cm.user.id),
                            cm.role,
                            cm.course.displayname,
                            str(cm.course.id),
                        )
                    )
                else:
                    messages.error(request, "Please provide a valid course code.")
            except AssertionError as err:
                messages.error(request, str(err))

        return HttpResponseRedirect("/course/list/")

    @staticmethod
    @login_required
    def import_ci(request, cid):
        "import calculated confidence intervals for Users"

        CoursePermissions.require_course_staff(request.user, cid)

        course = Course._default_manager.get(id=cid)

        render_dict = dict()

        render_dict["course"] = course
        render_dict["cid"] = course.id

        if request.method == 'POST':
            form = ImportStudentCIs(request.POST, request.FILES)
            if form.is_valid():
                update_supervised = form.cleaned_data['Update_supervisory_status']
                if update_supervised:
                    supervised_threshold= form.cleaned_data['Supervised_threshold']
                else:
                    supervised_threshold= None

                csv_file = request.FILES['file']
                # let's check if it is a csv file
                if not csv_file.name.endswith('.csv'):
                    messages.error(request, 'This is not a CSV file.')
                else:
                    count= CourseBase.import_student_cis(csv_file, supervised_threshold, cid)
                    messages.success(request, "Successfully uploaded %d student CIs." % count)
                    return HttpResponseRedirect(reverse("home:home"))
            else:
                messages.error(request, form.errors)
                form= ImportStudentCIs()
                render_dict["form"] = form
                render_dict["messages"] = get_messages(request)
                return render(request, "course-upload-cis.html", render_dict)


        form= ImportStudentCIs()
        render_dict["form"] = form
        return render(request, "course-upload-cis.html", render_dict)


    @staticmethod
    @login_required
    def export_ci(request, cid):
        "Export current confidence intervals for users"

        CoursePermissions.require_course_staff(request.user, cid)

        course = Course._default_manager.get(id=cid)
        students= CourseMember._default_manager.filter(course=course, active=True, role='student')

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="CIs_for_Students_in_%s (%s).csv"'
            % (course.displayname, timezone.now().strftime("%F-%X"))
        )


        writer = csv.writer(response)
        # FIXME: if student ID field changes
        headers = []
        headers += ["Student ID"]
        headers += ["Lower Confidence Bound", "Marking load", "Upper Confidence Bound"]
        headers += ["Qualification Status"]
        writer.writerow(headers)
        for stu in students:
            if stu.active:
                row = [stu.user.username]
                row.append(stu.lower_confidence_bound)
                row.append(stu.markingload)
                row.append(stu.upper_confidence_bound)
                row.append(stu.qualified)
                writer.writerow(row)

        return response

    @staticmethod
    @login_required
    def export_instructors(request, cid):
        "Export current confidence intervals for users"

        CoursePermissions.require_course_staff(request.user, cid)

        course = Course._default_manager.get(id=cid)
        instructors= CourseMember._default_manager.filter(course=course, active=True, role='instructor')

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="Instructor_list_in_course_%s.csv"'
            % (course.displayname)
        )


        writer = csv.writer(response)
        # FIXME: if student ID field changes
        headers = []
        headers += ["Instructor ID"]
        headers += ["First Name", "Last Name"]
        headers += ["Email Address"]
        writer.writerow(headers)
        for instructor in instructors:
            if instructor.active:
                row = [instructor.user.username]
                row.append(instructor.user.first_name)
                row.append(instructor.user.last_name)
                row.append(instructor.user.email)
                writer.writerow(row)

        return response

    @staticmethod
    @login_required
    def export_tas(request, cid):
        "Export current confidence intervals for users"

        CoursePermissions.require_course_staff(request.user, cid)

        course = Course._default_manager.get(id=cid)
        tas= CourseMember._default_manager.filter(course=course, active=True, role='ta')

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="TA_list_in_course_%s.csv"'
            % (course.displayname)
        )


        writer = csv.writer(response)
        # FIXME: if student ID field changes
        headers = []
        headers += ["TA ID"]
        headers += ["First Name", "Last Name"]
        headers += ["Email Address"]
        writer.writerow(headers)
        for ta in tas:
            if ta.active:
                row = [ta.user.username]
                row.append(ta.user.first_name)
                row.append(ta.user.last_name)
                row.append(ta.user.email)
                writer.writerow(row)

        return response

    @staticmethod
    @login_required
    def export_students(request, cid):
        "Export current confidence intervals for users"

        CoursePermissions.require_course_staff(request.user, cid)

        course = Course._default_manager.get(id=cid)
        students= CourseMember._default_manager.filter(course=course, active=True, role='student')

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="Student_list_in_course_%s.csv"'
            % (course.displayname)
        )


        writer = csv.writer(response)
        # FIXME: if student ID field changes
        headers = []
        headers += ["Student ID"]
        headers += ["First Name", "Last Name"]
        headers += ["Email Address"]
        writer.writerow(headers)
        for student in students:
            if student.active:
                row = [student.user.username]
                row.append(student.user.first_name)
                row.append(student.user.last_name)
                row.append(student.user.email)
                writer.writerow(row)

        return response

    @staticmethod
    def extract_date(participation):
        return participation.time_participated.date()

    @staticmethod
    @login_required
    @chosen_course_required
    def export_participation_data(request,  cid):

        # cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)
      
        CoursePermissions.require_instructor(request.user, course.id)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="Participation-Course-%s grades (%s).csv"'
            % (course.displayname, timezone.now().strftime("%F-%X"))
        )

        students = CourseMember.objects.filter(course_id = cid, role = 'student', active = True)
        participations = CourseParticipation.objects.filter(participant__course_id = cid, count_in_calculations = True ).order_by('time_participated')




        writer = csv.writer(response)
        # FIXME: if student ID field changes
        headers = []
        dates = []
        headers += ["Student name", "Student ID"]
        for time_participated, group in groupby(participations, key=CourseViews.extract_date):
            headers += [str(time_participated)+'-green (spoke)']
            headers += [str(time_participated)+'-blue (spoke)']
            headers += [str(time_participated)+'-red (spoke)']
            headers += [str(time_participated)+'-yellow (spoke)']
            headers += [str(time_participated)+'-Points gained']
            dates += [str(time_participated)]

        # headers += ["Student name", "Student ID"]
        writer.writerow(headers)

        # render_dict['students'] = list(students)
        for student in students:
            row=[student.get_user_fullname(), student.get_user_id()]
            for date in dates:
                student_participations = participations.filter(participant = student, time_participated__startswith= date)
                for i in range(1,5):
                    color_participation = student_participations.filter(participation_list = i)
                    
                    if color_participation.exists():
                        spoke = color_participation.filter(spoke_upon_participation= True)
                        if spoke.exists():
                            row.append(str(color_participation.count())+' ('+str(spoke.count())+')')
                        else:
                            row.append(str(color_participation.count())+ ' (0)')
                    else:
                        row.append('--')

                points_gained = 0
                if student_participations.exists:
                    for student_participation in student_participations:
                        points_gained += student_participation.participation_points_gained      
                row.append(str(points_gained))              


            writer.writerow(row)

        return response  


    @staticmethod
    @login_required
    @chosen_course_required
    def export_daily_participation_data(request,  cid):
        time_of_request = timezone.now()
        day_of_request = time_of_request.date()
        # cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)
      
        CoursePermissions.require_instructor(request.user, course.id)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="Daily-Participation-Course-%s grades (%s).csv"'
            % (course.displayname, timezone.now().strftime("%F-%X"))
        )

        students = CourseMember.objects.filter(course_id = cid, role = 'student', active = True)
        participations = CourseParticipation.objects.filter(participant__course_id = cid, time_participated__startswith = time_of_request.date(), count_in_calculations = True, real_participation = True ).order_by('time_participated')




        writer = csv.writer(response)
        # FIXME: if student ID field changes
        headers = []
        dates = []
        headers += ["Student name", "Student ID", "Points"]

        # for time_participated, group in groupby(participations, key=CourseViews.extract_date):
        #     headers += [str(time_participated)+'-green (spoke)']
        #     headers += [str(time_participated)+'-blue (spoke)']
        #     headers += [str(time_participated)+'-red (spoke)']
        #     headers += [str(time_participated)+'-Points gained']
        #     dates += [str(time_participated)]

        # headers += ["Student name", "Student ID"]
        writer.writerow(headers)

        # render_dict['students'] = list(students)

        list_lid = []
        if not course.points_upon_participation_in_green_list == 0:
            list_lid.append(1)
        if not course.points_upon_participation_in_blue_list == 0:
            list_lid.append(2)
        if not course.points_upon_participation_in_red_list == 0:
            list_lid.append(3)
        if not course.points_upon_participation_in_yellow_list == 0:
            list_lid.append(4)

        points_dict = {}
        for student in students:
            student_participations = participations.filter(participant = student, time_participated__startswith= time_of_request.date(), participation_list__in = list_lid)
            points_gained = 0
            if student_participations.exists:
                for student_participation in student_participations:
                    points_gained += student_participation.participation_points_gained      
            points_dict[student] = points_gained
            
        sorted_dict = {k: v for k, v in sorted(points_dict.items(), key=lambda item: item[1], reverse= True)}

        for student in sorted_dict:
            row=[student.get_user_fullname(), student.get_user_id()]
            row.append(sorted_dict[student])              
            writer.writerow(row)

        return response  
    
    @staticmethod
    @login_required
    @chosen_course_required
    def export_poll_participation(request, cid):
        poll_id = request.GET.get('poll_id', False)
        date = request.GET.get('date', False)
        course = Course._default_manager.get(id=cid)
        
        CoursePermissions.require_instructor(request.user, course.id)

        response = HttpResponse(content_type="text/csv")
        df_dict = None

        if poll_id:
            response["Content-Disposition"] = (
                'attachment; filename="Poll-Participation-Count-Poll-ID-%s-%s (%s).csv"'
                % (poll_id, course.displayname, timezone.now().strftime("%F-%X"))
            )

            df_dict = { "Student ID" : [], "Student Name" : [], "Poll Title" : [], "Poll Answer" : [], "Poll Start Time" : [], "Poll End Time" : [], "Student Answer" : [], "Time Answered" : []}

            students = CourseMember.objects.filter(course_id = cid, role = 'student', active = True)
            poll = Poll.objects.get(pk=poll_id)

            for student in students:
                student_id = student.user_id
                student_name = student.get_user_fullname()

                poll_results_student = PollResult.objects.filter(poll_id=poll_id, auth_user_id=student_id)

                if not poll_results_student.exists():
                    continue

                poll_result = poll_results_student.first()

                poll_title = poll.title
                poll_answer = poll.answer
                poll_start_time = poll.start_time
                poll_end_time = poll.end_time

                time_answered = poll_result.time
                student_answer = poll_result.answer

                df_dict["Student ID"].append(student_id)
                df_dict["Student Name"].append(student_name)
                df_dict["Poll Title"].append(poll_title)
                df_dict["Poll Answer"].append(poll_answer)
                df_dict["Poll Start Time"].append(poll_start_time)
                df_dict["Poll End Time"].append(poll_end_time)
                df_dict["Student Answer"].append(student_answer)
                df_dict["Time Answered"].append(time_answered)

        elif not poll_id:

            response["Content-Disposition"] = (
                'attachment; filename="Poll-Participation-Count-%s (%s).csv"'
                % (course.displayname, timezone.now().strftime("%F-%X"))
            )

            students = CourseMember.objects.filter(course_id = cid, role = 'student', active = True)
            lectures = Lecture.objects.filter(course_id = cid,
                                            start_time__startswith = date).order_by('time') if date else Lecture.objects.filter(course_id = cid).order_by('time')
            
            polls = Poll.objects.filter(lecture__in = lectures).order_by('lecture')

            df_dict = { "Student ID" : [], "Student Name" : [], "Lecture ID" : [], "Poll ID" : [], "Poll Title" : [], "Poll Answer" : [], "Poll Start Time" : [], "Poll End Time" : [], "Student Answer" : [], "Time Answered" : []}

            for student in students:
                student_id = student.user_id
                student_name = student.get_user_fullname()

                poll_results_student = PollResult.objects.filter(poll__in=polls, auth_user_id=student_id).order_by('poll')
                polls_student = Poll.objects.filter(poll_id__in=poll_results_student.values('poll_id')).order_by('lecture')

                if not poll_results_student.exists():
                    continue

                for poll in polls_student:
                    poll_id = poll.pk
                    poll_title = poll.title
                    poll_answer = poll.answer
                    poll_start_time = poll.start_time
                    poll_end_time = poll.end_time

                    poll_results_student_poll = poll_results_student.filter(poll=poll).order_by('poll')

                    for poll_result in poll_results_student_poll:
                        time_answered = poll_result.time
                        student_answer = poll_result.answer

                        lecture_id = poll.lecture.pk

                        df_dict["Student ID"].append(student_id)
                        df_dict["Student Name"].append(student_name)
                        df_dict["Lecture ID"].append(lecture_id)
                        df_dict["Poll ID"].append(poll_id)
                        df_dict["Poll Title"].append(poll_title)
                        df_dict["Poll Answer"].append(poll_answer)
                        df_dict["Poll Start Time"].append(poll_start_time)
                        df_dict["Poll End Time"].append(poll_end_time)
                        df_dict["Student Answer"].append(student_answer)
                        df_dict["Time Answered"].append(time_answered)

        df = pd.DataFrame(df_dict)
        df.to_csv(response, index=False)

        return response
    
    @staticmethod
    @login_required
    @chosen_course_required
    def export_messages(request, cid):
        date = request.GET.get('date', False)

        course = Course._default_manager.get(id=cid)
        CoursePermissions.require_instructor(request.user, course.id)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="Messages-%s (%s).csv"'
            % (course.displayname, timezone.now().strftime("%F-%X"))
        )

        df_dict = { "Message ID": [], "Lecture ID": [], "Student ID": [], "Message": [], "Message Time": [],  "Reply": [], "Blocked": [], "Hidden": [], "Broadcasted": []}

        if date:
            messages = Message.objects.filter(lecture__course_id = cid, time__startswith = date).order_by('lecture')
        elif not date:
            lectures = Lecture.objects.filter(course_id = cid).order_by('time')
            messages = Message.objects.filter(lecture__in = lectures).order_by('lecture')

        for message in messages:
            message_id = message.pk
            lecture_id = message.lecture.pk
            student_id = message.auth_user_id
            message_text = message.message
            message_time = message.time
            reply_text = message.reply_message
            blocked = message.blocked
            hidden = message.hidden
            broadcasted = message.broadcast

            df_dict["Message ID"].append(message_id)
            df_dict["Lecture ID"].append(lecture_id)
            df_dict["Student ID"].append(student_id)
            df_dict["Message"].append(message_text)
            df_dict["Message Time"].append(message_time)
            df_dict["Reply"].append(reply_text)
            df_dict["Blocked"].append(blocked)
            df_dict["Hidden"].append(hidden)
            df_dict["Broadcasted"].append(broadcasted)

        df = pd.DataFrame(df_dict)
        df.to_csv(response, index=False)

        return response