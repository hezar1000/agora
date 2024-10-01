from datetime import datetime
import hashlib
from django.conf import settings
from django.http import JsonResponse
from django.template import loader
from peer_course.base import CourseBase

from peer_home.wrappers import render

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import *

# Create your views here.

def get_poll_input(request):
    saved_poll_id = request.POST.get('saved-poll-id')
    poll_title = request.POST.get('poll-title')
    poll_data = request.POST.get('poll-data')
    anonymity = request.POST.get('options-anonymity')
    answer = request.POST.get('answer')
    answer_options = request.POST.getlist('answer-option')[:-1]
    no_answers = True if "true" in anonymity else False

    return saved_poll_id, poll_title, poll_data, anonymity, answer, answer_options, no_answers

def get_course_and_user_data(request):
        course_id = request.session["course_id"]
        auth_id = request.session['_auth_user_id']

        course = Course.objects.get(pk=course_id)
        coursemember = CourseBase.get_course_member(request.user, course.id)

        return int(auth_id), int(course_id), coursemember

class PollViews:
    
    @staticmethod
    def instructor(request):
        channel_layer = get_channel_layer()

        auth_id, course_id, course_member = get_course_and_user_data(request)
        
        data = {
            "auth_id": auth_id,
            "course_id": course_id,
            "course_member": course_member,
            "MEDIA_URL": settings.MEDIA_URL,
        }

        lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()

        if lecture:
            data["lecture"] = lecture
            poll = Poll.objects.filter(lecture_id=lecture.pk, end_time__isnull=True).first()

            if poll: data["poll"] = poll

        if request.method == 'POST':
            if 'enable-polling' in request.POST:
                polls = Poll.getSavedPolls(course_id)
                if len(polls) > 0:
                    data['saved_polls'] = polls
                    data['view_saved_polls'] = True
                else:
                    data['create_poll'] = True

                return render(request, 'instructor-poll.html', data)

            elif 'save-poll' in request.POST:
                saved_poll_id, poll_title, poll_data, anonymity, answer, answer_options, no_answers = get_poll_input(request)

                if not saved_poll_id:
                    poll = Poll.create(answer, no_answers, poll_title, course_id)
                    poll = Poll.saveAnswerOptions(poll.pk, answer_options)
                    poll = Poll.savePollText(poll.pk, poll_data)
                else:
                    poll = Poll.objects.get(poll_id=saved_poll_id)
                    poll.title = poll_title
                    poll.answer = answer
                    poll.dont_save_answer = no_answers
                    poll.save()
                    poll = Poll.saveAnswerOptions(poll.pk, answer_options)
                    poll = Poll.savePollText(poll.pk, poll_data)

                return render(request, 'instructor-poll.html', data)

            elif 'view-saved-polls' in request.POST:
                data['saved_polls'] = Poll.getSavedPolls(course_id)
                data['view_saved_polls'] = True
                return render(request, 'instructor-poll.html', data)
            
            elif 'view-create-poll' in request.POST:
                data['create_poll'] = True
                return render(request, 'instructor-poll.html', data)
            
            elif 'start-poll' in request.POST:
                saved_poll_id = request.POST.get('saved-poll-id')
                lecture_id = int(request.POST.get('id'))

                if not saved_poll_id:
                    poll_data = request.POST.get('poll-data')
                    poll_title = request.POST.get('poll-title')
                    anonymity = request.POST.get('options-anonymity')
                    answer = request.POST.get('answer')
                    answer_options = request.POST.getlist('answer-option')[:-1]

                    no_answers = True if "true" in anonymity else False
                    
                    poll = Poll.create(answer, no_answers, poll_title, course_id)
                    poll = Poll.saveAnswerOptions(poll.pk, answer_options)
                    if poll_data != "": poll = Poll.savePollText(poll.pk, poll_data)
                    poll = Poll.startPoll(poll.pk, lecture_id, datetime.now())
                else:
                    # if poll exists, create a duplicate with new poll_id and set is_duplicate to true
                    poll = Poll.objects.get(poll_id=saved_poll_id)
                    
                    if poll.end_time is None:
                        poll = Poll.startPoll(poll.pk, lecture_id, datetime.now())
                    else:
                        newPoll = Poll.create(poll.answer, poll.dont_save_answer, poll.title, course_id)
                        newPoll = Poll.saveAnswerOptions(newPoll.pk, json.loads(poll.answer_options))
                        newPoll = Poll.savePollText(newPoll.pk, poll.poll_data)
                        newPoll = Poll.setDuplicate(newPoll.pk, poll.pk)
                        
                        poll = newPoll
                        poll = Poll.startPoll(poll.pk, lecture_id, datetime.now())

                async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'start-poll',
                        'value': poll.pk,
                        'send_auth_id': auth_id
                    })

                data["lecture"] = Lecture.objects.get(lecture_id=lecture_id)
                data["poll"] = Poll.objects.get(poll_id=poll.pk)

                return render(request, 'instructor-poll.html', data)
            
            elif 'delete-poll' in request.POST:
                saved_poll_id = request.POST.get('saved-poll-id')
                poll = Poll.objects.get(poll_id=saved_poll_id)
                poll.delete()
                data['saved_polls'] = Poll.getSavedPolls(course_id)
                data['view_saved_polls'] = True
                return render(request, 'instructor-poll.html', data)
            
            elif 'edit-poll' in request.POST:
                saved_poll_id = request.POST.get('saved-poll-id')
                poll = Poll.objects.get(poll_id=saved_poll_id)
                data['edit_poll'] = {
                    "poll_id": poll.pk,
                    "poll_data": poll.poll_data,
                    "title": poll.title,
                    "answer_options": json.loads(poll.answer_options),
                    "answer": poll.answer,
                    "dont_save_answer": poll.dont_save_answer
                }
                data['create_poll'] = True
                
                return render(request, 'instructor-poll.html', data)
            
            elif 'clone-poll' in request.POST:
                saved_poll_id = request.POST.get('saved-poll-id')
                poll = Poll.objects.get(poll_id=saved_poll_id)

                newPoll = Poll.create(poll.answer, poll.dont_save_answer, poll.title + " (copy)", course_id)
                newPoll = Poll.saveAnswerOptions(newPoll.pk, json.loads(poll.answer_options))
                newPoll = Poll.savePollText(newPoll.pk, poll.poll_data)

                data['edit_poll'] = {
                    "poll_id": newPoll.pk,
                    "poll_data": newPoll.poll_data,
                    "title": newPoll.title,
                    "answer_options": json.loads(newPoll.answer_options),
                    "answer": newPoll.answer,
                    "dont_save_answer": newPoll.dont_save_answer
                }
                
                data['create_poll'] = True
                
                return render(request, 'instructor-poll.html', data)

            elif 'stop-poll' in request.POST:
                # lecture = data["lecture"]
                lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()

                if data.get("poll"):
                    poll = data["poll"]
                
                    async_to_sync(channel_layer.group_send)(
                        f'course_{course_id}',
                        {
                            'type': 'send_message',
                            'key': 'stop-poll',
                            'value': poll.pk,
                            'send_auth_id': auth_id
                        })
                    
                    # get poll with lecture_id and end_time = null
                    poll = Poll.endPoll(poll.pk, datetime.now())

                    results = Poll.getResults(poll.pk)
                    data["results"] = results
                    data["poll"] = None

                return render(request, 'instructor-poll.html', data)
        
        elif 'stop-poll' in request.GET:
            last_poll = Poll.lastPoll(lecture.pk)
            if last_poll:
                results = Poll.getResults(last_poll.pk)
                data["results"] = results

            return render(request, 'instructor-poll.html', data)
        
        elif 'toggle-lecture' in request.GET:
            print("toggle-lecture")
            if lecture:
                lecture_id = lecture.pk
                end_time = datetime.now()

                lecture = Lecture.endLecture(lecture_id, end_time)

                if data.get("poll"):
                    poll_id = data["poll"].pk
                    poll = Poll.endPoll(poll_id, end_time)
                    data["poll"] = None

                data["lecture"] = None

                async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'end-lecture',
                        'send_auth_id': auth_id
                    })
                
                return JsonResponse({'begin': False})
            else:
                lecture = Lecture.create(course_id)
                if lecture: data["lecture"] = Lecture.objects.get(lecture_id=lecture.pk)

                async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'begin-lecture',
                        'send_auth_id': auth_id
                    })

                return JsonResponse({'begin': True})

        elif 'update-results' in request.GET:
            poll_id = request.GET.get('update-results')
            results = Poll.getResults(poll_id)
            return JsonResponse(results, safe=False)

        else:
            return render(request, 'instructor-poll.html', data)
    
    @staticmethod    
    def student(request):
        channel_layer = get_channel_layer()

        auth_id, course_id, course_member = get_course_and_user_data(request)
        
        data = {
            "auth_id": auth_id,
            "course_id": course_id,
            "course_member": course_member,
            "MEDIA_URL": settings.MEDIA_URL,
        }

        lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()

        if lecture:
                data["lecture"] = lecture
                poll = Poll.objects.filter(lecture_id=lecture.pk, end_time__isnull=True).first()

                if poll: data["poll"] = poll

        if request.method == 'POST':

            auth_user_id = request.session['_auth_user_id']

            if 'poll-answer' in request.POST:
                poll_id = int(request.POST.get('poll_id'))
                answer = request.POST.get('poll-answer')

                result = PollResult.objects.filter(poll_id=poll_id, auth_user_id=auth_user_id).first()

                if result:
                    PollResult.updateResult(poll_id, auth_user_id, datetime.now(), answer)
                else:
                    result = PollResult.create(poll_id, auth_user_id, answer)

                return render(request, 'student-poll.html', data)

        else:
            if 'stop-poll' in request.GET:
                last_poll = Poll.lastPoll(lecture.pk)
                if last_poll:
                    results = Poll.getResults(last_poll.pk)
                    data["results"] = results

            return render(request, 'student-poll.html', data)
        
class MessageViews:
    @staticmethod
    def student(request):
        channel_layer = get_channel_layer()
        
        auth_id, course_id, course_member = get_course_and_user_data(request)
        
        data = {
            "auth_id": auth_id,
            "course_id": course_id,
            "course_member": course_member,
            "MEDIA_URL": settings.MEDIA_URL,
        }

        lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()

        if lecture:
            data["lecture"] = lecture
            
            messages = Message.getMessages(lecture.pk, True)
            data["messages"] = messages

            blocked = lecture.blocked_students.filter(pk=auth_id).first()
            if blocked:
                data["blocked"] = True
        
        if request.method == 'POST':
            if 'message' in request.POST:
                message = request.POST.get('message')
                course_id = request.session["course_id"]
                
                lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()
                
                if lecture:
                    message = Message.create(lecture.pk, auth_id, message)
                    if message is not None:
                        async_to_sync(channel_layer.group_send)(
                        f'course_{course_id}',
                        {
                            'type': 'send_message',
                            'key': 'message',
                            'value': message.pk,
                            'send_auth_id': auth_id
                        })
                        
                    return JsonResponse({'success': True})
                return JsonResponse({'success': True, 'error': 'Lecture not found'})
        
        return render(request, 'student-message.html', data)
                
    @staticmethod
    def instructor(request):
        channel_layer = get_channel_layer()

        auth_id, course_id, course_member = get_course_and_user_data(request)
        
        data = {
            "auth_id": auth_id,
            "course_id": course_id,
            "course_member": course_member,
            "MEDIA_URL": settings.MEDIA_URL,
        }

        lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()

        if lecture:
            data["lecture"] = lecture
            
            messages = Message.getMessages(lecture.pk, False)
            data["messages"] = messages
            
        if request.method == 'POST':
            auth_user_id = request.session['_auth_user_id']
            
            if "reply-message" in request.POST:
                reply = request.POST.get('reply-message')
                message_id = request.POST.get('message-id')
                
                Message.reply(message_id, reply)
                
                async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'reply-message',
                        'value': message_id,
                        'send_auth_id': auth_id
                    })
                
                return JsonResponse({'success': True})
                
            elif "block-user" in request.POST:
                student_id = request.POST.get('block-user')
                lecture_id = lecture.pk
                
                lecture = Lecture.blockStudent(lecture_id, student_id)
                
                async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'block-user',
                        'value': student_id,
                        'send_auth_id': auth_id
                    })
                
                return JsonResponse({'success': True})
                
            elif "unblock-user" in request.POST:
                student_id = request.POST.get('unblock-user')
                lecture_id = lecture.pk
                
                lecture = Lecture.unblockStudent(lecture_id, student_id)
                
                async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'unblock-user',
                        'value': student_id,
                        'send_auth_id': auth_id
                    })
                
                return JsonResponse({'success': True})
                
            elif 'broadcast-message' in request.POST:
                message_id = request.POST.get('broadcast-message')

                message = Message.broadcastMessage(message_id)

                async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'broadcast-message',
                        'value': message_id,
                        'send_auth_id': auth_id
                    })
                
                return JsonResponse({'success': True})
                
            elif "hide-message" in request.POST:
                message_id = request.POST.get('hide-message')
                
                message = Message.hideMessage(message_id)
                
                async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'hide-message',
                        'value': message_id,
                        'send_auth_id': auth_id
                    })
                
                return render(request, 'instructor-message.html', data)
            
        elif 'get-messages' in request.GET:
                last_message = data["messages"].last()
                
                template = loader.get_template('message-bubble.html')
                rendered_html = template.render({"message" : last_message})


                response_data = {
                    'html_content': rendered_html,
                }

                return JsonResponse(response_data)
        
        elif 'get-blocked-list' in request.GET:
            block_list = lecture.blocked_students.all()
            block_list = list(block_list.values())

            response_data = {
                'blocked_students': block_list,
            }

            return JsonResponse(response_data)

        elif 'toggle-message-board' in request.GET:
            toggle = request.GET.get('toggle-message-board')

            lecture = Lecture.toggleMessages(lecture.pk, toggle)

            async_to_sync(channel_layer.group_send)(
                    f'course_{course_id}',
                    {
                        'type': 'send_message',
                        'key': 'update-message-visibility',
                        'value': lecture.pk,
                        'send_auth_id': auth_id
                    })
            
            return JsonResponse({'success': True})

        return render(request, 'instructor-message.html', data)