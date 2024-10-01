
from django.shortcuts import render
from django.http import HttpResponseRedirect, HttpResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.conf import settings
from django.shortcuts import render as djangoRender
from django.http import JsonResponse
from django.contrib import messages
import numpy as np
from collections import defaultdict
import random
import heapq
import atexit
from django.db.models import Count

from itertools import chain

from peer_home.wrappers import render
from peer_course.models import Course, CourseMember, CourseParticipation
from peer_course.decorators import chosen_course_required
from peer_course.base import CourseBase
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from peer_course.base import CoursePermissions

# Functions other than view functions should be placed elsewhere

from peer_lecture.views import *

from datetime import timedelta, time


class HomeViews:
    @staticmethod
    def render(request):
        "Render the homepage"

        render_dict = {"logged_in": request.user.is_authenticated}
        if request.user.is_authenticated:
            # We actually have a good enough dashboard in course_list
            return HomeViews.dashboard(request)
            # render_dict['pending_assignments'] = (AssignmentBase
            #     .get_user_assignments_by_status(request.user)['pending'])
            # render_dict['reviews'] = ReviewBase.get_user_review_by_status(request.user)
            # return render(request, 'dashboard.html', render_dict)
        # return djangoRender(request, 'index.html', render_dict)

        # return HttpResponseRedirect(reverse(settings.LOGIN_URL))
        return HttpResponseRedirect(reverse(settings.LOGIN_URL))

    @staticmethod
    @chosen_course_required
    def dashboard(request):
        render_dict = dict()

        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)
        request.session["course_id"] = course.id
        request.session['course_role'] = course.id

        coursemember = CourseBase.get_course_member(request.user, course.id)

        render_dict["coursemember"] = coursemember

        # client_ip = request.META.get('HTTP_X_FORWARDED_FOR')

        # if not client_ip:
        #     client_ip = request.META.get('REMOTE_ADDR')

        PollViews.instructor(request)
        MessageViews.instructor(request)

        render_dict['lecture'] = Lecture.currentLecture(course.id)

        if "next" in request.GET:
            return HttpResponseRedirect(request.GET["next"])
        
        if course.enable_participation == False: 
            render_dict["course"] = course

       
            return render(request, "course-view-redesign.html", render_dict)

        else:
            
            render_dict["course"] = course
            render_dict["enable_participation"] = True


            return render(request, "course-view.html", render_dict)
    
    @staticmethod
    def random_student_helper(request, lid):
        channel_layer = get_channel_layer()

        # lid = int(lid)
        # if lid == 1 or lid ==2:
        #     list_lid = [1,2]
        # elif lid ==3:
        #     list_lid = [3]

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        render_dict = dict()
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        time_of_request = timezone.now()

        lid = int(lid)
        list_lid = []
        if not course.points_upon_participation_in_green_list == 0:
            list_lid.append(1)
        if not course.points_upon_participation_in_blue_list == 0:
            list_lid.append(2)
        if not course.points_upon_participation_in_red_list == 0:
            list_lid.append(3)
        if not course.points_upon_participation_in_yellow_list == 0:
            list_lid.append(4)

        if lid == 1 and course.points_upon_participation_in_green_list == 0:
            list_lid = [1]
        if lid == 2 and course.points_upon_participation_in_blue_list == 0:
            list_lid = [2]
        if lid == 3 and course.points_upon_participation_in_red_list == 0:
            list_lid = [3]
        if lid == 4 and course.points_upon_participation_in_yellow_list == 0:
            list_lid = [4]


        if lid ==1:
            color = 'Green'
            students = CourseMember.objects.filter(course = course, hand_up= True
                ,role='student', active= True)
            activate = coursemember.hand_up 
            participation_points_to_gain = course.points_upon_participation_in_green_list
        if lid ==2:
            color = 'Blue'
            students = CourseMember.objects.filter(course = course, hand_up_list_2= True
                ,role='student', active= True)
            activate = coursemember.hand_up_list_2
            participation_points_to_gain = course.points_upon_participation_in_blue_list
        if lid ==3: 
            color = 'Red'
            students = CourseMember.objects.filter(course = course, hand_up_list_3= True
                ,role='student', active= True)
            activate = coursemember.hand_up_list_3
            participation_points_to_gain = course.points_upon_participation_in_red_list
        if lid ==4: 
            color = 'Yellow'
            students = CourseMember.objects.filter(course = course, hand_up_list_4= True
                ,role='student', active= True)
            activate = coursemember.hand_up_list_4
            participation_points_to_gain = course.points_upon_participation_in_yellow_list

        if coursemember.role == 'instructor':
            if activate == False:
                render_dict['student'] = "You should enable the hand up feature for the "+color+" list first."
                render_dict['student_id']= 'None'
                return JsonResponse(render_dict)

            all_participations = CourseParticipation.objects.filter(
                participant__in=students,
                participation_list__in=list_lid,
                count_in_calculations=True
            )
            # print(students)
            # print(all_participations)

            std_participations = all_participations.filter(
                time_participated__startswith = time_of_request.date(),
            )

            # Perform bulk queries
            existing_participations = std_participations.values('participant').distinct()

            spoken_participations = std_participations.filter(spoke_upon_participation=True).values('participant').distinct()

            all_spoken_counts = all_participations.filter(spoke_upon_participation=True).values('participant').annotate(
                spoken_count=Count('id')
            )

            # Create dictionaries for faster lookup
            existing_participations_set = set(p['participant'] for p in existing_participations)

            spoken_participations_set = set(p['participant'] for p in spoken_participations)

            all_spoken_dict = {p['participant']: max(p['spoken_count'], 2) for p in all_spoken_counts}

            # Prepare bulk create list
            participations_to_create = []

            list_of_spoken = []
            list_of_unspoken = []
            list_of_spoken_counts = []
            list_of_unspoken_counts = []

            if students.exists():
                for student in students:
                    # Create new participation
                    new_participation = CourseParticipation(
                        participant=student,
                        time_participated=time_of_request,
                        participation_list=lid,
                        spoke_upon_participation=False
                    )

                    if student.id not in existing_participations_set:
                        new_participation.participation_points_gained = participation_points_to_gain
                    else:
                        new_participation.participation_points_gained = participation_points_to_gain * course.fraction_of_points_gained_upon_further_participations

                    # Handle spoken/unspoken logic
                    spoken_count = all_spoken_dict.get(student.id, 2)
                    inverse_log_count = 1 / np.log2(spoken_count)
                    if student.id in spoken_participations_set and student not in list_of_spoken:
                        list_of_spoken.append(student)
                        list_of_spoken_counts.append(inverse_log_count)
                        new_participation.real_participation = False
                    elif student.id not in spoken_participations_set and student not in list_of_unspoken:
                        list_of_unspoken.append(student)
                        list_of_unspoken_counts.append(inverse_log_count)

                    # Append new_participation after all modifications
                    participations_to_create.append(new_participation)

                # Bulk create
                CourseParticipation.objects.bulk_create(participations_to_create)

                # Convert to numpy arrays and normalize
                list_of_spoken_counts = np.array(list_of_spoken_counts)
                list_of_unspoken_counts = np.array(list_of_unspoken_counts)

                list_of_spoken_counts_norm = np.divide(list_of_spoken_counts, np.sum(list_of_spoken_counts)) if list_of_spoken_counts.size > 0 else np.array([])
                list_of_unspoken_counts_norm = np.divide(list_of_unspoken_counts, np.sum(list_of_unspoken_counts)) if list_of_unspoken_counts.size > 0 else np.array([])

                if list_of_unspoken:
                    unspoken_student = np.random.choice(list_of_unspoken, p = list_of_unspoken_counts_norm)

                    # unspoken_student = random.choice(list_of_unspoken)
                    render_dict['student']= unspoken_student.user.first_name +' '+unspoken_student.user.last_name
                    render_dict['student_id']= unspoken_student.user.username

                    if lid == 1:
                        unspoken_student.hand_up = False
                    elif lid == 2:
                        unspoken_student.hand_up_list_2 = False
                    elif lid == 3:
                        unspoken_student.hand_up_list_3 = False
                    elif lid == 4:
                        unspoken_student.hand_up_list_4 = False
                    unspoken_student.save()

                    participation = CourseParticipation.objects.get(
                        participant = unspoken_student,
                        time_participated = time_of_request
                    )
                    participation.spoke_upon_participation = True
                    participation.save()
                    
                elif list_of_spoken:
                    # spoken_students = students_to_speak.filter(spoken='True').order_by('?')
                    
                        # for std in spoken_students:
                        #     list_of_spoken.append(std.user.first_name +' '+std.user.last_name)
                        spoken_student = np.random.choice(list_of_spoken, p = list_of_spoken_counts_norm)

                        # spoken_student = random.choice(list_of_spoken)
                        # spoken_student.hand_up = False
                        if lid == 1:
                            spoken_student.hand_up = False
                        elif lid ==2:
                            spoken_student.hand_up_list_2 = False
                        elif lid ==3:
                            spoken_student.hand_up_list_3 = False
                        elif lid ==4:
                            spoken_student.hand_up_list_4 = False
                        spoken_student.save()

                        render_dict['student']= spoken_student.user.first_name +' '+spoken_student.user.last_name
                         
                        render_dict['student_id']= spoken_student.user.username
                        participation = CourseParticipation.objects.get(
                            participant = spoken_student,
                            time_participated = time_of_request   
                        )
                        participation.spoke_upon_participation = True
                        participation.real_participation = True
                        participation.save()

                participations_to_consider = CourseParticipation.objects.filter(
                    participant__course = course,
                    time_participated__startswith = time_of_request.date(),
                    spoke_upon_participation= True,
                    participation_list__in = list_lid,
                    count_in_calculations = True
                )

                # Get all distinct participant IDs for the bonus credit
                students_for_bonus_credit = set(participations_to_consider.values_list('participant_id', flat=True))

                # Fetch existing participations in one query
                existing_participations = set(
                    CourseParticipation.objects.filter(
                        participant_id__in=students_for_bonus_credit,
                        time_participated=time_of_request,
                        participation_list=lid,
                        count_in_calculations=True
                    ).values_list('participant_id', flat=True)
                )

                # Prepare the new participations to be created in bulk
                new_participations = []

                date_first_class_ends = timezone.now()
                new_time = date_first_class_ends.replace(hour=23, minute=59)
                time_threshold = time(hour = 16, minute = 59)
                # Iterate over student IDs and determine if new participations need to be created
                for std_id in students_for_bonus_credit:
                    if std_id not in existing_participations:
                        if participations_to_consider.filter(participant_id=std_id, time_participated__lte=new_time).exists() and timezone.localtime(timezone.now()).time() > time_threshold:
                        # if False:
                            new_participations.append(
                                CourseParticipation(
                                    participant_id=std_id,
                                    time_participated=time_of_request,
                                    participation_points_gained=participation_points_to_gain * course.fraction_of_points_gained_upon_further_participations,
                                    spoke_upon_participation=False,
                                    participation_list=lid,
                                    real_participation=False,
                                    count_in_calculations=False
                                )
                            )
                        else:
                            new_participations.append(
                                CourseParticipation(
                                    participant_id=std_id,
                                    time_participated=time_of_request,
                                    participation_points_gained=participation_points_to_gain * course.fraction_of_points_gained_upon_further_participations,
                                    spoke_upon_participation=False,
                                    participation_list=lid,
                                    real_participation=False,
                                    count_in_calculations=True
                                )
                            )

                # Bulk create all new participations at once
                CourseParticipation.objects.bulk_create(new_participations)

            else:
                render_dict['student']= 'No available student in the '+color+' list.'
                render_dict['student_id']= 'None'

            if coursemember.role == 'instructor':
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'next-student',
                    'send_auth_id': coursemember.id,
                    'value' : render_dict['student_id']
                })
                
        
        else:
            render_dict['student']= 'You are not an instructor in this course.'
        return JsonResponse(render_dict) 
        # channel_layer = get_channel_layer()
        # cid = request.session.get("course_id")
        # course = Course.objects.filter(id=cid).first()

        # CoursePermissions.require_instructor(request.user, cid)
        # coursemember = CourseBase.get_course_member(request.user, course.id)
        
        # render_dict = {}

        # lid = int(lid)
        # lid_map = {
        #     1: {'color': 'Green', 'points': course.points_upon_participation_in_green_list, 'list': 'hand_up'},
        #     2: {'color': 'Blue', 'points': course.points_upon_participation_in_blue_list, 'list': 'hand_up_list_2'},
        #     3: {'color': 'Red', 'points': course.points_upon_participation_in_red_list, 'list': 'hand_up_list_3'},
        #     4: {'color': 'Yellow', 'points': course.points_upon_participation_in_yellow_list, 'list': 'hand_up_list_4'},
        # }

        # color = lid_map[lid]['color']
        # list_name = lid_map[lid]['list']
        # activate = getattr(coursemember, list_name)

        # if not activate:
        #     render_dict['student'] = f"You should enable the hand up feature for the {color} list first."
        #     render_dict['student_id'] = 'None'
        #     return JsonResponse(render_dict)

        # time_of_request = timezone.now()
        # students = CourseMember.objects.filter(course=course, role='student', active=True, **{list_name: True})
        # all_participations = CourseParticipation.objects.filter(
        #     participant__in=students,
        #     participation_list=lid,
        #     count_in_calculations=True
        # )

        # std_participations = all_participations.filter(time_participated__date=time_of_request.date())
        
        # if students.exists():
        #     # Fetch all spoken counts at once
        #     spoken_counts = std_participations.filter(spoke_upon_participation=True).values('participant').annotate(count=Count('participant'))
        #     spoken_counts_dict = {item['participant']: item['count'] for item in spoken_counts}

        #     # Create a dictionary to store spoken counts for each student
        #     student_counts = defaultdict(int)
        #     for student in students:
        #         student_counts[student] = spoken_counts_dict.get(student, 0)

        #     # Determine unspoken students
        #     unspoken_students = [student for student, count in student_counts.items() if count == 0]

        #     # If there are unspoken students, select one randomly
        #     if unspoken_students:
        #         unspoken_counts = {student: 1 / np.log2(count or 2) for student, count in student_counts.items()}
        #         selected_student = random.choices(list(unspoken_counts.keys()), weights=list(unspoken_counts.values()), k=1)[0]
        #     # If all students have spoken, select one randomly from spoken students
        #     elif student_counts:
        #         spoken_counts = {student: 1 / np.log2(count or 2) for student, count in student_counts.items()}
        #         selected_student = random.choices(list(spoken_counts.keys()), weights=list(spoken_counts.values()), k=1)[0]
        #     # If no students are available
        #     else:
        #         render_dict['student'] = f'No available student in the {color} list.'
        #         render_dict['student_id'] = 'None'
        #         return JsonResponse(render_dict)

        #     selected_student_name = f"{selected_student.user.first_name} {selected_student.user.last_name}"
        #     render_dict['student'] = selected_student_name
        #     render_dict['student_id'] = selected_student.user.username

        #     setattr(selected_student, list_name, False)
        #     selected_student.save()

        #     participation, _ = CourseParticipation.objects.get_or_create(
        #         participant=selected_student,
        #         time_participated=time_of_request,
        #         participation_points_gained=lid_map[lid]['points'],
        #         participation_list=lid
        #     )
        #     participation.spoke_upon_participation = True
        #     participation.save()

        #     students_for_bonus_credit = all_participations.values_list('participant', flat=True).distinct()
            
        #     for student_id in students_for_bonus_credit:
        #         if not CourseParticipation.objects.filter(
        #             participant_id=student_id,
        #             time_participated=time_of_request,
        #             participation_list=lid,
        #             count_in_calculations=True
        #         ).exists():
        #             CourseParticipation.objects.create(
        #                 participant_id=student_id,
        #                 time_participated=time_of_request,
        #                 participation_points_gained=lid_map[lid]['points'] * course.fraction_of_points_gained_upon_further_participations,
        #                 spoke_upon_participation=False,
        #                 participation_list=lid,
        #                 real_participation=False,
        #                 count_in_calculations=True
        #             )

        # else:
        #     render_dict['student'] = f'No available student in the {color} list.'
        #     render_dict['student_id'] = 'None'

        # if coursemember.role == 'instructor':
        #     async_to_sync(channel_layer.group_send)(
        #         f'course_{course.id}',
        #         {
        #             'type': 'send_message',
        #             'key': 'next-student',
        #             'send_auth_id': coursemember.id,
        #             'value': render_dict['student_id']
        #         }
        #     )

        # return JsonResponse(render_dict)



    @staticmethod
    @login_required
    @chosen_course_required
    def random_student(request):
        return HomeViews.random_student_helper(request, 1)

    @staticmethod
    @login_required
    @chosen_course_required
    def random_student_blue(request):
        return HomeViews.random_student_helper(request, 2)


    @staticmethod
    @login_required
    @chosen_course_required
    def random_student_red(request):
        return HomeViews.random_student_helper(request, 3)

    @staticmethod
    @login_required
    @chosen_course_required
    def random_student_yellow(request):
        return HomeViews.random_student_helper(request, 4)

    @staticmethod
    @login_required
    @chosen_course_required
    def choose_next(request, uid, lid):

        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        render_dict = dict()
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        chosen_student = CourseMember.objects.filter(id=uid)
        time_of_request = timezone.now()

        lid = int(lid)
        list_lid = []
        if not course.points_upon_participation_in_green_list == 0:
            list_lid.append(1)
        if not course.points_upon_participation_in_blue_list == 0:
            list_lid.append(2)
        if not course.points_upon_participation_in_red_list == 0:
            list_lid.append(3)
        if not course.points_upon_participation_in_yellow_list == 0:
            list_lid.append(4)

        if lid == 1 and course.points_upon_participation_in_green_list == 0:
            list_lid = [1]
        if lid == 2 and course.points_upon_participation_in_blue_list == 0:
            list_lid = [2]
        if lid == 3 and course.points_upon_participation_in_red_list == 0:
            list_lid = [3]
        if lid == 4 and course.points_upon_participation_in_yellow_list == 0:
            list_lid = [4]

        if coursemember.role == 'instructor':
        #     if coursemember.hand_up == False:
        #         render_dict['student'] = "You should enable the hand up feature for the Green list first."
        #         render_dict['student_id']= 'None'
        #         return JsonResponse(render_dict)

            if lid == 1 : 
                students = CourseMember.objects.filter(course = course, hand_up= True
                    ,role='student', active= True) 
                participation_points_to_gain = course.points_upon_participation_in_green_list
                color = 'Green'

            elif lid == 2:
                students = CourseMember.objects.filter(course = course, hand_up_list_2= True
                    ,role='student', active= True) 
                participation_points_to_gain = course.points_upon_participation_in_blue_list
                color = 'Blue'
            elif lid == 3: 
                students = CourseMember.objects.filter(course = course, hand_up_list_3= True
                    ,role='student', active= True) 
                participation_points_to_gain = course.points_upon_participation_in_red_list
                color = 'Red'
            elif lid == 4: 
                students = CourseMember.objects.filter(course = course, hand_up_list_4= True
                    ,role='student', active= True) 
                participation_points_to_gain = course.points_upon_participation_in_yellow_list
                color = 'Yellow'

            if students.exists():
                for student in students:
                    if not CourseParticipation.objects.filter(
                        participant = student,
                        time_participated__startswith = time_of_request.date(),
                        participation_list__in = list_lid,
                        count_in_calculations = True
                    ).exists():
                        CourseParticipation.objects.create(
                            participant  = student, 
                            time_participated = time_of_request,
                            participation_points_gained = participation_points_to_gain,
                            spoke_upon_participation = False,
                            participation_list= lid
                        )
                    else:
                        CourseParticipation.objects.create(
                            participant  = student, 
                            time_participated = time_of_request,
                            participation_points_gained = participation_points_to_gain * course.fraction_of_points_gained_upon_further_participations,
                            participation_list= lid
                        )


                participations_to_consider = CourseParticipation.objects.filter(
                    participant__course = course,
                    time_participated__startswith = time_of_request.date(),
                    spoke_upon_participation= True,
                    participation_list__in = list_lid,
                    count_in_calculations = True
                )

                students_for_bonus_credit = list(participations_to_consider.values_list('participant',flat= True).distinct())


                for std_id in students_for_bonus_credit:
                    if  not CourseParticipation.objects.filter(
                            participant_id  = std_id, 
                            time_participated = time_of_request,
                            participation_list= lid,
                            count_in_calculations = True
                        ).exists():
                        date_first_class_ends = timezone.now()
                        date_first_class_ends.replace(hour =22, minute = 00)
                        CourseParticipation.objects.create(
                                participant_id  = std_id, 
                                time_participated = time_of_request,
                                participation_points_gained = participation_points_to_gain * course.fraction_of_points_gained_upon_further_participations,
                                spoke_upon_participation = False,
                                participation_list= lid,
                                real_participation = False,
                                count_in_calculations = True
                            )

            
            students_to_speak = chosen_student

            if students_to_speak.exists():
                if not CourseParticipation.objects.filter(
                        participant = chosen_student[0],
                        time_participated__startswith = time_of_request.date(),
                        participation_list = lid,
                        spoke_upon_participation = True,
                        count_in_calculations = True
                    ).exists():
                    unspoken_student = chosen_student[0]
                    render_dict['student']= unspoken_student.user.first_name +' '+unspoken_student.user.last_name
                    render_dict['color'] = color
                    render_dict['student_id']= unspoken_student.user.username

                    if lid == 1:
                        unspoken_student.hand_up = False
                    elif lid ==2:
                        unspoken_student.hand_up_list_2 = False
                    elif lid ==3:
                        unspoken_student.hand_up_list_3 = False
                    elif lid ==4:
                        unspoken_student.hand_up_list_4 = False
                    unspoken_student.save()

                    new_participation = CourseParticipation.objects.get(
                        participant = unspoken_student,
                        time_participated = time_of_request
                    )
                    new_participation.spoke_upon_participation = True
                    new_participation.save()
                    
                else:
                    spoken_students = chosen_student
                    if spoken_students.exists():
                        spoken_student= spoken_students[0]
                        if lid == 1:
                            spoken_student.hand_up = False
                        elif lid ==2:
                            spoken_student.hand_up_list_2 = False
                        elif lid ==3:
                            spoken_student.hand_up_list_3 = False
                        elif lid ==4:
                            spoken_student.hand_up_list_4 = False
                        spoken_student.save()

                        render_dict['student']= spoken_student.user.first_name +' '+spoken_student.user.last_name
                        render_dict['color'] = color
                        render_dict['student_id']= spoken_student.user.username
                        new_participation = CourseParticipation.objects.get(
                            participant = spoken_student,
                            time_participated = time_of_request   
                        )
                        new_participation.spoke_upon_participation = True
                        new_participation.save()

            if coursemember.role == 'instructor':
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'next-student',
                    'send_auth_id': coursemember.id,
                    'value' : render_dict['student_id']
                })

        else:
            render_dict['student']= 'You are not an instructor in this course.'
   
        return JsonResponse(render_dict) 



    @staticmethod
    @login_required
    @chosen_course_required
    def clear_all(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        if coursemember.role == 'instructor':
            coursemember.time_spoken = timezone.now()
            coursemember.save()

            students = CourseMember.objects.filter(course = course, hand_up= True, 
                role='student', active= True)

            for student in students:
                student.hand_up = False
                student.save()

            if coursemember.role == 'instructor':
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'clear-hands',
                    'send_auth_id': coursemember.id
                })
                

        return HttpResponse('hands were cleared out.')

    @staticmethod
    @login_required
    @chosen_course_required
    def clear_all_lists(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)

        if coursemember.role == "instructor":
            coursemember.time_spoken = timezone.now()
            coursemember.save()
            
            students = CourseMember.objects.filter(course = course, role='student', hand_up= True, active= True) | CourseMember.objects.filter(course = course, role='student', hand_up_list_2= True, active= True) | CourseMember.objects.filter(course = course, role='student', hand_up_list_3= True, active= True) | CourseMember.objects.filter(course = course, role='student', hand_up_list_4= True, active= True)
            for student in students:
                student.hand_up= False
                student.hand_up_list_2= False
                student.hand_up_list_3= False
                student.hand_up_list_4= False
                student.save()

            if coursemember.role == 'instructor':
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'clear-hands',
                    'send_auth_id': coursemember.id
                })

        return HttpResponse('hands were cleared out.')

    @staticmethod
    @login_required
    @chosen_course_required
    def clear_all_blue(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        if coursemember.role == 'instructor':
            coursemember.time_spoken = timezone.now()
            coursemember.save()
            
            students = CourseMember.objects.filter(course = course, hand_up_list_2= True, 
                role='student', active= True)

            for student in students:
                student.hand_up_list_2 = False
                student.save()

            if coursemember.role == 'instructor':
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'clear-hands',
                    'send_auth_id': coursemember.id
                })

        return HttpResponse('Blue hands were cleared out.')

    @staticmethod
    @login_required
    @chosen_course_required
    def clear_all_red(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        if coursemember.role == 'instructor':
            coursemember.time_spoken = timezone.now()
            coursemember.save()
            
            students = CourseMember.objects.filter(course = course, hand_up_list_3= True, 
                role='student', active= True)

            for student in students:
                student.hand_up_list_3 = False
                student.save()

            if coursemember.role == 'instructor':
                async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'clear-hands',
                        'send_auth_id': coursemember.id
                    })

        return HttpResponse('Red hands were cleared out.')


    @staticmethod
    @login_required
    @chosen_course_required
    def clear_all_yellow(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        if coursemember.role == 'instructor':
            students = CourseMember.objects.filter(course = course, hand_up_list_4= True, 
                role='student', active= True)

            for student in students:
                student.hand_up_list_4 = False
                student.save()

            if coursemember.role == 'instructor':
                async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'clear-hands',
                        'send_auth_id': coursemember.id
                    })

        return HttpResponse('Yellow hands were cleared out.')



    @staticmethod
    @login_required
    @chosen_course_required
    def enable(request):
        return HomeViews.enable_list_helper(request,1)
        # render_dict = dict()
        # cid = request.session["course_id"]
        # CoursePermissions.require_course_member(request.user, cid)
        # course = Course._default_manager.get(id=cid)
        # coursemember = CourseBase.get_course_member(request.user, course.id)

        # if coursemember.hand_up == False:

        #     if coursemember.role == "instructor":
        #         coursemember.hand_up = True
        #         coursemember.save()
        #         render_dict['hand']= 'The hand up feature is enabled for the Green list.'

        #     if coursemember.role == 'student':
        #         instructors = CourseMember.objects.filter(course = course, role= 'instructor')
        #         if instructors.exists():
        #             for instructor in instructors:
        #                 if instructor.hand_up == True:
        #                     coursemember.hand_up = True
        #                     coursemember.save()
        #                     render_dict['hand'] = 'Your hand is up in the Green list. '
        #                 else:
        #                     render_dict['hand'] = 'The Green list is disabled by the instructor for now.'
                            

        #     # return JsonResponse(render_dict)
        # else:
        #     pass
        #     # coursemember.hand_up = False
        #     # coursemember.save()
        #     # if coursemember.role == "instructor":
        #     #     students = CourseMember.objects.filter(course = course, hand_up= True, 
        #     #         role='student', active= True)
        #     #     for student in students:
        #     #         student.hand_up= False
        #     #         student.save()
        #     # render_dict['hand'] = 'hand up was disabled.'
        
        # return JsonResponse(render_dict)

    @staticmethod
    @login_required
    @chosen_course_required
    def enable_blue(request):
        return HomeViews.enable_list_helper(request,2)

    @staticmethod
    @login_required
    @chosen_course_required
    def enable_red(request):
        return HomeViews.enable_list_helper(request,3)

    @staticmethod
    @login_required
    @chosen_course_required
    def enable_yellow(request):
        return HomeViews.enable_list_helper(request,4)

    @staticmethod
    @login_required
    @chosen_course_required
    def enable_all(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)

        coursemember.hand_up = True
        coursemember.hand_up_list_2 = True
        coursemember.hand_up_list_3 = True
        coursemember.hand_up_list_4 = True
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role == "instructor" and send_message:
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'enable-hand',
                    'send_auth_id': cid
                })

        return HttpResponse('Hand up was enabled in all lists.')


    def enable_list_helper(request, lid):
        channel_layer = get_channel_layer()

        render_dict = dict()
        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role == "instructor":
            if lid == 1:
                coursemember.hand_up = True
                coursemember.time_spoken = timezone.now()
                coursemember.save()
                color = 'Green'

                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': coursemember.id
                    })

            if lid == 2:
                coursemember.hand_up_list_2 = True
                coursemember.time_spoken = timezone.now()
                coursemember.save()
                color = 'Blue'

                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': coursemember.id
                    })
            if lid == 3:
                coursemember.hand_up_list_3 = True
                coursemember.time_spoken = timezone.now()
                coursemember.save()
                color = 'Red'

                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': coursemember.id
                    })

            if lid == 4:
                coursemember.hand_up_list_4 = True
                coursemember.time_spoken = timezone.now()
                coursemember.save()
                color = 'Yellow'

                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': coursemember.id
                    })
  
        if coursemember.role == 'student':
            if coursemember.hand_up == True or coursemember.hand_up_list_2 == True or coursemember.hand_up_list_3 == True or coursemember.hand_up_list_4 == True:
                render_dict['hand'] = '<span style="color: red">  You can only raise your hand in one list at the time. </span>'
            else:
                instructors = CourseMember.objects.filter(course = course, role= 'instructor')
                
                if instructors.exists():
                    ref_time = instructors[0].time_spoken
                    for instructor in instructors:
                        if ref_time < instructor.time_spoken:
                            ref_time = instructor.time_spoken
                else:
                    ref_time = timezone.now()
            
                if lid ==1:
                    if instructors.exists():
                        for instructor in instructors:
                            if instructor.hand_up == True:
                                coursemember.hand_up= True
                                if coursemember.time_spoken < ref_time:
                                    coursemember.time_spoken = timezone.now()
                                coursemember.save()
                                render_dict['hand'] = 'Your hand is up in the Green list. '
                            else:
                                render_dict['hand'] = 'The Green list is disabled by the instructor for now.'

                        async_to_sync(channel_layer.group_send)(
                        f'course_{course.id}',
                        {
                            'type': 'send_message',
                            'key': 'update-hand-status',
                            'send_auth_id': coursemember.id
                        })
                                
                if lid ==2:
                    if instructors.exists():
                        for instructor in instructors:
                            if instructor.hand_up_list_2 == True:
                                coursemember.hand_up_list_2 = True
                                if coursemember.time_spoken < ref_time:
                                    coursemember.time_spoken = timezone.now()
                                coursemember.save()
                                render_dict['hand'] = 'Your hand is up in the Blue list. '
                            else:
                                render_dict['hand'] = 'The Blue list is disabled by the instructor for now.'

                        async_to_sync(channel_layer.group_send)(
                        f'course_{course.id}',
                        {
                            'type': 'send_message',
                            'key': 'update-hand-status',
                            'send_auth_id': coursemember.id
                        })
                            
                if lid ==3: 
                    if instructors.exists():
                        for instructor in instructors:
                            if instructor.hand_up_list_3 == True:
                                coursemember.hand_up_list_3 = True
                                if coursemember.time_spoken < ref_time:
                                    coursemember.time_spoken = timezone.now()
                                coursemember.save()
                                render_dict['hand'] = 'Your hand is up in the Red list. '
                            else:
                                render_dict['hand'] = 'The Red list is disabled by the instructor for now.'

                        async_to_sync(channel_layer.group_send)(
                        f'course_{course.id}',
                        {
                            'type': 'send_message',
                            'key': 'update-hand-status',
                            'send_auth_id': coursemember.id
                        })
                            
                if lid ==4: 
                    if instructors.exists():
                        for instructor in instructors:
                            if instructor.hand_up_list_4 == True:
                                coursemember.hand_up_list_4 = True
                                if coursemember.time_spoken < ref_time:
                                    coursemember.time_spoken = timezone.now()
                                coursemember.save()
                                render_dict['hand'] = 'Your hand is up in the Yellow list. '
                            else:
                                render_dict['hand'] = 'The Yellow list is disabled by the instructor for now.'

                        async_to_sync(channel_layer.group_send)(
                        f'course_{course.id}',
                        {
                            'type': 'send_message',
                            'key': 'update-hand-status',
                            'send_auth_id': coursemember.id
                        })
                            
        return JsonResponse(render_dict)

    # @staticmethod
    # @login_required
    # @chosen_course_required
    # def disable(request):
    #     cid = request.session["course_id"]
    #     CoursePermissions.require_course_member(request.user, cid)

    #     course = Course._default_manager.get(id=cid)
    #     coursemember = CourseBase.get_course_member(request.user, course.id)
        
    #     coursemember.hand_up = False
    #     coursemember.save()

    #     if coursemember.role == "instructor":
    #         students = CourseMember.objects.filter(course = course, hand_up= True, 
    #             role='student', active= True)
    #         for student in students:
    #             student.hand_up= False
    #             student.save()

    #     return HttpResponse('hand up was disabled.')


    # @staticmethod
    # @login_required
    # @chosen_course_required
    # def enable_blue(request):
    #     render_dict = dict()
    #     cid = request.session["course_id"]
    #     CoursePermissions.require_course_member(request.user, cid)
    #     course = Course._default_manager.get(id=cid)
    #     coursemember = CourseBase.get_course_member(request.user, course.id)
        
    #     if coursemember.role == "instructor":
    #         coursemember.hand_up_list_2 = True
    #         coursemember.save()
    #         render_dict['hand']= 'The hand up feature is enabled for the Blue list.'
            
    #     if coursemember.role == 'student':
    #         instructors = CourseMember.objects.filter(course = course, role= 'instructor')
    #         if instructors.exists():
    #             for instructor in instructors:
    #                 if instructor.hand_up_list_2 == True:
    #                     coursemember.hand_up_list_2 = True
    #                     coursemember.save()
    #                     render_dict['hand'] = 'Your hand is up in the Blue list. '
    #                 else:
    #                     render_dict['hand'] = 'The Blue list is disabled by the instructor for now.'
                         

    #     return JsonResponse(render_dict)


    # @staticmethod
    # @login_required
    # @chosen_course_required
    # def enable_red(request):
    #     render_dict = dict()
    #     cid = request.session["course_id"]
    #     CoursePermissions.require_course_member(request.user, cid)
    #     course = Course._default_manager.get(id=cid)
    #     coursemember = CourseBase.get_course_member(request.user, course.id)
        
    #     if coursemember.role == "instructor":
    #         coursemember.hand_up_list_3 = True
    #         coursemember.save()
    #         render_dict['hand']= 'The hand up feature is enabled for the Red list.'
            
    #     if coursemember.role == 'student':
    #         instructors = CourseMember.objects.filter(course = course, role= 'instructor')
    #         if instructors.exists():
    #             for instructor in instructors:
    #                 if instructor.hand_up_list_3 == True:
    #                     coursemember.hand_up_list_3 = True
    #                     coursemember.save()
    #                     render_dict['hand'] = 'Your hand is up in the Red list. '
    #                 else:
    #                     render_dict['hand'] = 'The Red list is disabled by the instructor for now.'
                         

    #     return JsonResponse(render_dict)

    @staticmethod
    @login_required
    @chosen_course_required
    def disable(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        coursemember.hand_up = False
        coursemember.save()
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role == "student":
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': coursemember.id
                })

        if coursemember.role == "instructor":
            students = CourseMember.objects.filter(course = course, hand_up= True, 
                role='student', active= True)
            for student in students:
                student.hand_up= False
                student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'disable-hand',
                        'send_auth_id': coursemember.id
                    })

        return HttpResponse('hand up was disabled.')

    @staticmethod
    @login_required
    @chosen_course_required
    def disable_blue(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        coursemember.hand_up_list_2 = False
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role == "student":
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': coursemember.id
                })

        if coursemember.role == "instructor":
            students = CourseMember.objects.filter(course = course, hand_up_list_2= True, 
                role='student', active= True)
            for student in students:
                student.hand_up_list_2= False
                student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'disable-hand',
                    'send_auth_id': coursemember.id
                })

        return HttpResponse('hand up was disabled for the Blue list.')

    @staticmethod
    @login_required
    @chosen_course_required
    def disable_red(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        coursemember.hand_up_list_3 = False
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role == "student":
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': coursemember.id
                })

        if coursemember.role == "instructor":
            students = CourseMember.objects.filter(course = course, hand_up_list_3= True, 
                role='student', active= True)
            for student in students:
                student.hand_up_list_3= False
                student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'disable-hand',
                    'send_auth_id': coursemember.id
                })

        return HttpResponse('hand up was disabled for the Red list.')

    @staticmethod
    @login_required
    @chosen_course_required
    def disable_yellow(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        coursemember.hand_up_list_4 = False
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role == "student":
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': coursemember.id
                })

        if coursemember.role == "instructor":
            students = CourseMember.objects.filter(course = course, hand_up_list_4= True, 
                role='student', active= True)
            for student in students:
                student.hand_up_list_4= False
                student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'disable-hand',
                    'send_auth_id': coursemember.id
                })

        return HttpResponse('hand up was disabled for the Yellow list.')


    @staticmethod
    @login_required
    @chosen_course_required
    def disable_all(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)

        coursemember.hand_up = False
        coursemember.hand_up_list_2 = False
        coursemember.hand_up_list_3 = False
        coursemember.hand_up_list_4 = False
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role == "student":
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': coursemember.id
                })

        if coursemember.role == "instructor":
            students = CourseMember.objects.filter(course = course, role='student', hand_up= True, active= True) | CourseMember.objects.filter(course = course, role='student', hand_up_list_2= True, active= True) | CourseMember.objects.filter(course = course, role='student', hand_up_list_3= True, active= True) | CourseMember.objects.filter(course = course, role='student', hand_up_list_4= True, active= True)
            for student in students:
                student.hand_up= False
                student.hand_up_list_2= False
                student.hand_up_list_3= False
                student.hand_up_list_4= False
                student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'disable-hand',
                    'send_auth_id': coursemember.id
                })

        return HttpResponse('Hand up was disabled in all lists.')



    @staticmethod
    @login_required
    @chosen_course_required
    def check_status(request):
        render_dict = dict()
        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)
        CoursePermissions.require_course_member(request.user, cid)
        coursemember = CourseBase.get_course_member(request.user, cid)

        instructors = CourseMember.objects.filter(course = course, role= 'instructor')

        if not instructors.filter(hand_up= True).exists():
            render_dict['disbale_green'] = True
        else:
            render_dict['disbale_green'] = False
            if coursemember.hand_up == False:
                render_dict['status_green'] = 'Your hand is down in the Green list.'
            else:
                render_dict['status_green'] = 'Your hand is up in the Green list. '

        instructors = CourseMember.objects.filter(course = course, role= 'instructor')
        if not instructors.filter(hand_up_list_2= True).exists():
            render_dict['disbale_blue'] = True
        else:
            render_dict['disbale_blue'] = False
            if coursemember.hand_up_list_2 == False:
                render_dict['status_blue'] = 'Your hand is down in the Blue list.'
            else:
                render_dict['status_blue'] = 'Your hand is up in the Blue list. '

        instructors = CourseMember.objects.filter(course = course, role= 'instructor')
        if not instructors.filter(hand_up_list_3= True).exists():
            render_dict['disbale_red'] = True
        else:
            render_dict['disbale_red'] = False
            if coursemember.hand_up_list_3 == False:
                render_dict['status_red'] = 'Your hand is down in the Red list.'
            else:
                render_dict['status_red'] = 'Your hand is up in the Red list. '

        instructors = CourseMember.objects.filter(course = course, role= 'instructor')
        if not instructors.filter(hand_up_list_4= True).exists():
            render_dict['disbale_yellow'] = True
        else:
            render_dict['disbale_yellow'] = False
            if coursemember.hand_up_list_4 == False:
                render_dict['status_yellow'] = 'Your hand is down in the Yellow list.'
            else:
                render_dict['status_yellow'] = 'Your hand is up in the Yellow list. '


        # if coursemember.hand_up_list_2 == False:
        #     render_dict['status_blue'] = 'Your hand is down in the Blue list.'
        # if coursemember.hand_up_list_3 == False:
        #     render_dict['status_red'] = 'Your hand is down in the Red list.'
        # if coursemember.hand_up_list_4 == False:
        #     render_dict['status_yellow'] = 'Your hand is down in the Yellow list.'

        # if coursemember.hand_up == True:
        #     render_dict['status_green'] = 'Your hand is up in the Green list. '
        # if coursemember.hand_up_list_2 == True:
        #     render_dict['status_blue'] = 'Your hand is up in the Blue list. '
        # if coursemember.hand_up_list_3 == True:
        #     render_dict['status_red'] = 'Your hand is up in the Red list. '
        # if coursemember.hand_up_list_4 == True:
        #     render_dict['status_yellow'] = 'Your hand is up in the Yellow list. '

        time_of_request = timezone.now()

        participations_total = CourseParticipation.objects.filter(
            participant = coursemember,
            count_in_calculations = True
        )
        participations_today = participations_total.filter(
            time_participated__startswith = time_of_request.date(), 
            real_participation= True
        )
        participations_bonus = participations_total.filter(
            time_participated__startswith = time_of_request.date(),
            real_participation = False 
        )

        total_points = 0 
        total_points_today = 0 
        total_points_bonus = 0
        for participation in participations_total:
            total_points += participation.participation_points_gained

        for participation in participations_today:
            total_points_today += participation.participation_points_gained

        for participation in participations_bonus:
            total_points_bonus += participation.participation_points_gained
        
        # render_dict["total_points"]= total_points
        render_dict["total_points_today"]= total_points_today
        render_dict["total_bonus_points"]= total_points_bonus




        return JsonResponse(render_dict)



    @staticmethod
    @login_required
    @chosen_course_required
    def count_hands_up(request):
        channel_layer = get_channel_layer()

        # CoursePermissions.require_instructor(request.user, cid)
        time_of_request = timezone.now()
        render_dict = dict()
        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)
        list_of_spoken= []
        list_of_unspoken=[]
        blue_list_of_spoken= []
        blue_list_of_unspoken=[]
        red_list_of_spoken= []
        red_list_of_unspoken=[]
        yellow_list_of_spoken= []
        yellow_list_of_unspoken=[]

        spoken_students = []
        blue_spoken_students = []
        red_spoken_students = []
        yellow_spoken_students = []

        coursemember = CourseBase.get_course_member(request.user, course.id)


        if coursemember.hand_up == True:
            render_dict['green_enabled'] = True
        else:
            render_dict['green_enabled'] = False
        
        if coursemember.hand_up_list_2 == True:
            render_dict['blue_enabled'] = True
        else:
            render_dict['blue_enabled'] = False
        if coursemember.hand_up_list_3 == True:   
            render_dict['red_enabled'] = True
        else:
            render_dict['red_enabled'] = False
        if coursemember.hand_up_list_4 == True: 
            render_dict['yellow_enabled'] = True
        else:
            render_dict['yellow_enabled'] = False
            
        if not coursemember.hand_up == True and  not coursemember.hand_up_list_2 == True  and  not coursemember.hand_up_list_3 == True and  not coursemember.hand_up_list_4 == True:
            render_dict['count'] = 'Feature disabled.'
            render_dict['count_spoken'] = 'Feature disabled.'
            render_dict['count_total'] = [0,0,0,0]
            render_dict['count_total_spoken'] = [0,0,0,0]
        else:
            all_students = CourseMember.objects.filter(course = course, role='student', active= True)
            students = all_students.filter(hand_up= True).order_by('time_spoken')

            spoken_participations = CourseParticipation.objects.filter(
                participant__course = course,
                time_participated__startswith = time_of_request.date(),
                spoke_upon_participation = True,
                count_in_calculations = True
            ).order_by('-time_participated')
            
            all_spoken_students = spoken_participations.filter(participation_list__in = [1,2,3,4])
            # green_already_spoken_count = len(all_spoken_students.order_by().values_list('participant',flat= True).distinct())
            spoken_students = list(all_spoken_students.filter(participant__hand_up = True).order_by().values_list('participant',flat= True).distinct())
            green_already_spoken_count = len(spoken_students)


            blue_students = all_students.filter(hand_up_list_2= True).order_by('time_spoken')
            # all_blue_spoken_students = spoken_participations.filter(participation_list = 2)
            all_blue_spoken_students = all_spoken_students
            # blue_already_spoken_count = len(all_blue_spoken_students.order_by().values_list('participant',flat= True).distinct())
            blue_spoken_students = list(all_blue_spoken_students.filter(participant__hand_up_list_2 = True).order_by().values_list('participant',flat= True).distinct())
            blue_already_spoken_count = len(blue_spoken_students)



            red_students = all_students.filter(hand_up_list_3= True).order_by('time_spoken')
            # all_red_spoken_students = spoken_participations.filter(participation_list = 3)
            all_red_spoken_students = all_spoken_students
            # red_already_spoken_count = len(all_red_spoken_students.order_by().values_list('participant',flat= True).distinct())
            red_spoken_students = list(all_red_spoken_students.filter(participant__hand_up_list_3 = True).order_by().values_list('participant',flat= True).distinct())
            red_already_spoken_count = len(red_spoken_students)



            yellow_students = all_students.filter(hand_up_list_4= True).order_by('time_spoken')
            # all_yellow_spoken_students = spoken_participations.filter(participation_list = 4)
            all_yellow_spoken_students = all_spoken_students
            # yellow_already_spoken_count = len(all_yellow_spoken_students.order_by().values_list('participant',flat= True).distinct())
            yellow_spoken_students = list(all_yellow_spoken_students.filter(participant__hand_up_list_4 = True).order_by().values_list('participant',flat= True).distinct())
            yellow_already_spoken_count = len(yellow_spoken_students)


            spoken_lists = spoken_students + blue_spoken_students + red_spoken_students + yellow_spoken_students
            spoken_lists_final = list(set(spoken_lists))

            list_of_unspoken = [[x.user.first_name,x.user.last_name,x.id] for x in list(students) if x.id not in spoken_lists_final]
            blue_list_of_unspoken = [[x.user.first_name,x.user.last_name,x.id] for x in list(blue_students) if x.id not in spoken_lists_final]
            red_list_of_unspoken = [[x.user.first_name,x.user.last_name,x.id] for x in list(red_students) if x.id not in spoken_lists_final]
            yellow_list_of_unspoken = [[x.user.first_name,x.user.last_name,x.id] for x in list(yellow_students) if x.id not in spoken_lists_final]


            unspoken_count= len(list_of_unspoken)
            spoken_count = len(spoken_students)
            blue_unspoken_count= len(blue_list_of_unspoken)
            blue_spoken_count = len(blue_spoken_students)
            red_unspoken_count= len(red_list_of_unspoken)
            red_spoken_count = len(red_spoken_students)
            yellow_unspoken_count= len(yellow_list_of_unspoken)
            yellow_spoken_count = len(yellow_spoken_students)

            render_dict['count'] = 'Unspoken: '+'Green: '+ str(unspoken_count) + ', Blue: ' + str(blue_unspoken_count) + ', Red: ' + str(red_unspoken_count) + ', Yellow: ' + str(yellow_unspoken_count) + '</br>' + 'Spoken:&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;' + 'Green: '+ str(spoken_count) + ', Blue: ' + str(blue_spoken_count) + ', Red: ' + str(red_spoken_count) + ', Yellow: ' + str(yellow_spoken_count)
            render_dict['count_spoken'] =  'Green: '+ str(green_already_spoken_count) + ', Blue: ' + str(blue_already_spoken_count) + ', Red: ' + str(red_already_spoken_count) + ', Yellow: ' + str(yellow_already_spoken_count) + '</br>' + '&nbsp;'           
            render_dict['count_total'] = [
                unspoken_count+spoken_count,
                blue_unspoken_count+blue_spoken_count,
                red_unspoken_count+red_spoken_count,
                yellow_unspoken_count+yellow_spoken_count
                ]
            render_dict['count_total_spoken'] = [
                green_already_spoken_count,
                blue_already_spoken_count,
                red_already_spoken_count,
                yellow_already_spoken_count
                ]




            hand_raise_times = []
            list_of_spoken_temp = []
            for std_id in spoken_students:
                std = CourseMember.objects.get(id= std_id)
                hand_raise_times.append(std.time_spoken)
                list_of_spoken_temp.append([std.user.first_name,std.user.last_name,std.id])
                
            list_of_spoken = [x for _, x in sorted(zip(hand_raise_times, list_of_spoken_temp))]

            hand_raise_times_blue = []
            blue_list_of_spoken_temp = []
            for std_id in blue_spoken_students:
                std = CourseMember.objects.get(id= std_id)
                hand_raise_times_blue.append(std.time_spoken)
                blue_list_of_spoken_temp.append([std.user.first_name,std.user.last_name,std.id])

            blue_list_of_spoken = [x for _, x in sorted(zip(hand_raise_times_blue, blue_list_of_spoken_temp))]

            hand_raise_times_red = []
            red_list_of_spoken_temp = []
            for std_id in red_spoken_students:
                std = CourseMember.objects.get(id= std_id)
                hand_raise_times_red.append(std.time_spoken)
                red_list_of_spoken_temp.append([std.user.first_name,std.user.last_name,std.id])
            
            red_list_of_spoken = [x for _, x in sorted(zip(hand_raise_times_red, red_list_of_spoken_temp))]

            hand_raise_times_yellow = []
            yellow_list_of_spoken_temp = []
            for std_id in yellow_spoken_students:
                std = CourseMember.objects.get(id= std_id)
                hand_raise_times_yellow.append(std.time_spoken)
                yellow_list_of_spoken_temp.append([std.user.first_name,std.user.last_name,std.id])


            yellow_list_of_spoken = [x for _, x in sorted(zip(hand_raise_times_yellow, yellow_list_of_spoken_temp))]


        render_dict['list_of_unspoken'] = list_of_unspoken
        render_dict['blue_list_of_unspoken'] = blue_list_of_unspoken
        render_dict['red_list_of_unspoken'] = red_list_of_unspoken
        render_dict['yellow_list_of_unspoken'] = yellow_list_of_unspoken
        
        render_dict['list_of_spoken'] = list_of_spoken
        render_dict['blue_list_of_spoken'] = blue_list_of_spoken
        render_dict['red_list_of_spoken'] = red_list_of_spoken
        render_dict['yellow_list_of_spoken'] = yellow_list_of_spoken

        # get send_message data
        send_message = request.GET.get('send_message') == "true"
        if coursemember.role == "instructor" and send_message:
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'enable-hand',
                    'send_auth_id': coursemember.id
                })

        return JsonResponse(render_dict)


    @staticmethod
    @login_required
    @chosen_course_required
    def count_already_spoken(request):

        time_of_request = timezone.now()
        render_dict = dict()
        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)

        list_of_already_spoken= []

        coursemember = CourseBase.get_course_member(request.user, course.id)
        if not coursemember.hand_up == True and  not coursemember.hand_up_list_2 == True  and  not coursemember.hand_up_list_3 == True and  not coursemember.hand_up_list_4 == True:   
            render_dict['count_spoken'] = 'Feature disabled.'
        else:
            spoken_participations = CourseParticipation.objects.filter(
                participant__course = course,
                time_participated__startswith = time_of_request.date(),
                spoke_upon_participation = True,
                count_in_calculations = True
            ).order_by('-time_participated')

            already_spoken = []
            already_spoken_names = []

            for participation in spoken_participations:
                already_spoken_names.append([participation.participant, participation.participation_list])
                already_spoken.append([participation.participant, 'Green' if participation.participation_list == 1 else 'Blue' if participation.participation_list == 2 else 'Red' if participation.participation_list == 3 else 'Yellow'])

            for std in already_spoken:
                list_of_already_spoken.append([ std[0].user.first_name,std[0].user.last_name, std[1], '-' ])

        render_dict['list_of_already_spoken'] = list_of_already_spoken
        return JsonResponse(render_dict)


    @staticmethod
    @login_required
    @chosen_course_required
    def undo(request):
        channel_layer = get_channel_layer()
        
        render_dict = dict()
        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        # CoursePermissions.require_instructor(request.user, cid)
        if coursemember.role == 'instructor':
            time_of_request = timezone.now()
            participations = CourseParticipation.objects.filter(
                participant__course = course,
                time_participated__startswith = time_of_request.date(),
                count_in_calculations = True
            ).order_by('-time_participated')
            if participations.exists():
                time_of_interest = participations[0].time_participated
                participations_to_remove = CourseParticipation.objects.filter(
                participant__course = course,
                time_participated = time_of_interest,
                count_in_calculations = True
                )
                # speaker_participation = participations_to_remove.filter(spoke_upon_participation= True)
                # if speaker_participation.exists():
                #     lid = speaker_participation[0].participation_list
                #     speaker = speaker_participation[0].participant
                #     if lid ==1:
                #         speaker.hand_up = True
                #     if lid ==2:
                #         speaker.hand_up_list_2 = True
                #     if lid ==3:
                #         speaker.hand_up_list_3 = True
                #     speaker.save()
                
                for participation_to_remove in participations_to_remove:
                    participation_to_remove.count_in_calculations = False
                    participation_to_remove.save()

                send_message = request.GET.get('send_message') == "true"
                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'undo-last-call',
                        'send_auth_id': coursemember.id
                    })


        time_of_request = timezone.now()
        render_dict = dict()
        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)

        list_of_already_spoken= []

        coursemember = CourseBase.get_course_member(request.user, course.id)
        if not coursemember.hand_up == True and  not coursemember.hand_up_list_2 == True  and  not coursemember.hand_up_list_3 == True and  not coursemember.hand_up_list_4 == True:   
            render_dict['count_spoken'] = 'Feature disabled.'
        else:
            spoken_participations = CourseParticipation.objects.filter(
                participant__course = course,
                time_participated__startswith = time_of_request.date(),
                spoke_upon_participation = True,
                count_in_calculations = True
            ).order_by('-time_participated')

            already_spoken = []
            already_spoken_names = []
            green_already_spoken = []
            blue_already_spoken = []
            red_already_spoken = []
            yellow_already_spoken = []

            for participation in spoken_participations:
                if participation.participant not in already_spoken_names:
                    already_spoken_names.append(participation.participant)
                    already_spoken.append([participation.participant, 'Green' if participation.participation_list == 1 else 'Blue' if participation.participation_list == 2 else 'Red' if participation.participation_list == 3 else 'Yellow'])

                if (participation.participation_list==1) and (participation.participant not in green_already_spoken):
                    green_already_spoken.append(participation.participant)

                if (participation.participation_list==2) and (participation.participant not in blue_already_spoken):
                    blue_already_spoken.append(participation.participant)

                if (participation.participation_list==3) and (participation.participant not in red_already_spoken):
                    red_already_spoken.append(participation.participant)

                if (participation.participation_list==4) and (participation.participant not in yellow_already_spoken):
                    yellow_already_spoken.append(participation.participant)

            green_already_spoken_count = len(green_already_spoken)
            blue_already_spoken_count = len(blue_already_spoken)
            red_already_spoken_count = len(red_already_spoken)
            yellow_already_spoken_count = len(yellow_already_spoken)

            render_dict['count_spoken'] =  'Count: '+'Green: '+ str(green_already_spoken_count) + ', Blue: ' + str(blue_already_spoken_count) + ', Red: ' + str(red_already_spoken_count) + ', Yellow: ' + str(yellow_already_spoken_count) + '</br>' + '&nbsp;'

            for std in already_spoken:
                list_of_already_spoken.append([ std[0].user.first_name,std[0].user.last_name, std[1], '-' ])

        render_dict['list_of_already_spoken'] = list_of_already_spoken
        return JsonResponse(render_dict)


    @staticmethod
    @login_required
    @chosen_course_required
    def disqualify_all(request):
        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)

        course = Course._default_manager.get(id=cid)
        students = CourseMember.objects.filter(course = course, 
            role='student', active= True)
        for student in students:
            student.qualified = False
            student.save()
        #message.sucess('Every student was disqualified.')
        return HttpResponse('Every student was disqualified.')


    @staticmethod
    @login_required
    @chosen_course_required
    def reset_class_participation(request):
        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        students = CourseMember.objects.filter(course = course, 
            role='student', active= True)
        for student in students:
            student.spoken = False
            student.first_hand_up= True
            student.save()
        return HttpResponse('Every student was cleared for class participation.')

    @staticmethod
    @login_required
    @chosen_course_required
    def reset_participation_points(request):
        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)

        course = Course._default_manager.get(id=cid)
        students = CourseMember.objects.filter(course = course, 
            role='student', active= True)
        for student in students:
            student.participation_points = 0
            student.save()
        return HttpResponse('Every student was cleared for weekly participation points.')

    @staticmethod
    def update_course(request):
        render_dict = dict()
        if request.method == "POST":
            cid = request.POST.get("course")
            request.session["cid"] = cid
            url_next = request.GET.get("next", "/")
            return HttpResponseRedirect(url_next)

        return djangoRender(request, "dashboard.html", render_dict)

    @staticmethod
    def start_timer(request):
        render_dict = dict()
        cid = request.session["course_id"]
        CoursePermissions.require_course_staff(request.user, cid)
        course = Course._default_manager.get(id=cid)
        ta = CourseBase.get_course_member(request.user, course.id)
        participations = CourseParticipation.objects.filter(participant = ta).order_by('-id')
        # print (participations)
        if participations.exists():
            if participations[0].participation_list == 11:  
                CourseParticipation.objects.create(
                    participant  = ta, 
                    time_participated = timezone.now(),
                    spoke_upon_participation = False,
                    participation_list= 10,
                    real_participation = True,
                    count_in_calculations = True
                )
                render_dict['response'] = 'Your timer has started!'
            else:  
                render_dict['response'] = 'You have a running timer already!'
                return JsonResponse(render_dict)   
        else: 
            CourseParticipation.objects.create(
                participant  = ta, 
                time_participated = timezone.now(),
                spoke_upon_participation = False,
                participation_list= 10,
                real_participation = True,
                count_in_calculations = True
            )
            render_dict['response'] = 'Your timer has started!'
        return JsonResponse(render_dict) 


    @staticmethod
    def stop_timer(request):
        render_dict = dict()
        cid = request.session["course_id"]
        CoursePermissions.require_course_staff(request.user, cid)
        course = Course._default_manager.get(id=cid)
        ta = CourseBase.get_course_member(request.user, course.id)
        participations = CourseParticipation.objects.filter(participant = ta).order_by('-id')
        if participations.exists():
            if participations[0].participation_list == 10:  
                CourseParticipation.objects.create(
                    participant  = ta, 
                    time_participated = timezone.now(),
                    spoke_upon_participation = False,
                    participation_list= 11,
                    real_participation = True,
                    count_in_calculations = True
                )
                render_dict['response'] = 'Your timer has stopped!'
            else:
                render_dict['response'] = 'You do not have an active timer!'
        else:
            render_dict['response'] = 'You do not have an active timer!'
        return JsonResponse(render_dict) 





def call_if_callable(item):
    if callable(item):
        return item()
    return item

