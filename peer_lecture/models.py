from django.db import models

# Create your models here.
import os
from django.db import models
from peer_course.models import Course
from django.contrib.auth.models import User
import json

# Create your models here.


class Lecture(models.Model):
    lecture_id = models.AutoField(primary_key=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True)
    blocked_students = models.ManyToManyField(User, related_name='blocked_students')
    enable_messages = models.BooleanField(default=True)

    def create(course_id):
        lecture = Lecture(course_id=course_id)
        lecture.save()
        return lecture

    def endLecture(lecture_id, time):
        lecture = Lecture.objects.get(pk=lecture_id)
        lecture.end_time = time
        lecture.save()
        return lecture

    def currentLecture(course_id):
        lecture = Lecture.objects.filter(
            course_id=course_id, end_time__isnull=True).first()
        return lecture

    def blockStudent(lecture_id, student_id):
        lecture = Lecture.objects.get(pk=lecture_id)
        student = User.objects.get(pk=student_id)
        messages = Message.objects.filter(
            lecture_id=lecture_id, auth_user_id=student_id)

        lecture.blocked_students.add(student)
        lecture.save()

        for message in messages:
            message.blocked = True
            message.save()

        return lecture
    
    def unblockStudent(lecture_id, student_id):
        lecture = Lecture.objects.get(pk=lecture_id)
        student = User.objects.get(pk=student_id)

        lecture.blocked_students.remove(student)
        lecture.save()

        return lecture
    
    def toggleMessages(lecture_id, toggle):
        lecture = Lecture.objects.get(pk=lecture_id)
        lecture.enable_messages = True if toggle == "true" else False
        lecture.save()
        return lecture

    class Meta:
        db_table = "peer_lecture"


class Poll(models.Model):
    poll_id = models.AutoField(primary_key=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, null=True)
    title = models.CharField(max_length=200)
    duplicate_of = models.ForeignKey('self', on_delete=models.CASCADE, null=True)
    start_time = models.DateTimeField(null=True)
    end_time = models.DateTimeField(null=True)
    poll_data = models.TextField(null=True)
    answer = models.CharField(max_length=200)
    answer_options = models.TextField(null=True)
    dont_save_answer = models.BooleanField()

    def create(answer, dont_save_answer, title, course_id):
        poll = Poll(answer=answer, dont_save_answer=dont_save_answer, title=title, course_id=course_id)
        poll.save()
        return poll
    
    def setDuplicate(poll_id, duplicate_id):
        poll = Poll.objects.get(pk=poll_id)
        poll.duplicate_of_id = duplicate_id
        poll.save()
        return poll

    def savePollText(poll_id, poll_data):
        poll = Poll.objects.get(pk=poll_id)
        poll.poll_data = poll_data if poll_data != "" else None
        poll.save()
        return poll

    def saveAnswerOptions(poll_id, answer_options):
        poll = Poll.objects.get(pk=poll_id)
        poll.answer_options = json.dumps(answer_options)
        poll.save()
        return poll

    def startPoll(poll_id, lecture_id, start_time):
        poll = Poll.objects.get(pk=poll_id)
        poll.start_time = start_time
        poll.lecture_id = lecture_id
        poll.save()
        return poll
    
    def getAnswerOptions(poll_id):
        poll = Poll.objects.get(pk=poll_id)
        
        return json.loads(poll.answer_options)

    def endPoll(poll_id, end_time):
        poll = Poll.objects.get(pk=poll_id)
        poll.end_time = end_time
        poll.save()
        return poll

    def lastPoll(lecture_id):
        poll = Poll.objects.filter(
            lecture_id=lecture_id).order_by('-end_time').first()
        return poll
    
    def getSavedPolls(course_id):
        polls_orig = Poll.objects.filter(duplicate_of__isnull=True, course_id=course_id).order_by('title')
        polls = []

        for poll in polls_orig:
            p = poll
            p.answer_options = json.loads(p.answer_options)
            polls.append(p)
        
        return polls

    def getResults(poll_id):
        poll = Poll.objects.get(pk=poll_id)
        dont_save_ans = poll.dont_save_answer

        if dont_save_ans:
            return None
        else:
            votes = PollResult.objects.filter(poll_id=poll_id).values('answer').annotate(count=models.Count('answer')).order_by('-count')
            answer = poll.answer
            answer_options = json.loads(poll.answer_options)
            
            results = {}
            
            for option in answer_options:
                results[option] = 0
                
            for vote in votes:
                results[vote["answer"]] = vote["count"]

            return json.dumps({"results": results, "answer": answer, "poll_id": poll.pk})
        
class PollResult(models.Model):
    result_id = models.AutoField(primary_key=True)
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE)
    auth_user = models.ForeignKey(User, on_delete=models.CASCADE)
    time = models.DateTimeField(auto_now_add=True)
    answer = models.CharField(max_length=200, null=True)

    def create(poll_id, auth_user_id, answer):
        result = PollResult(poll_id=poll_id, auth_user_id=auth_user_id)
        poll = Poll.objects.get(pk=poll_id)
        dont_save_ans = poll.dont_save_answer

        if dont_save_ans:
            result.answer = ""
        else:
            result.answer = answer

        result.save()
        return result

    def updateResult(poll_id, auth_user_id, time, answer):
        result = PollResult.objects.get(poll_id=poll_id, auth_user_id=auth_user_id)
        result.time = time

        poll = Poll.objects.get(pk=poll_id)
        dont_save_ans = poll.dont_save_answer

        if dont_save_ans:
            result.answer = ""
        else:
            result.answer = answer

        result.save()
        return result


class Message(models.Model):
    message_id = models.AutoField(primary_key=True)
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE)
    auth_user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField()
    time = models.DateTimeField(auto_now_add=True)
    reply_message = models.TextField(null=True)
    blocked = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)
    broadcast = models.BooleanField(default=False)

    def create(lecture_id, auth_user_id, message):
        lecture = Lecture.objects.get(pk=lecture_id)
        if lecture.blocked_students.filter(pk=auth_user_id).exists():
            return None

        message = Message(lecture_id=lecture_id,
                          auth_user_id=auth_user_id, message=message)
        message.save()
        return message

    def reply(message_id, reply_message):
        message = Message.objects.get(pk=message_id)
        message.reply_message = reply_message
        message.save()
        return message
    
    def hideMessage(message_id):
        message = Message.objects.get(pk=message_id)
        message.hidden = True
        message.save()
        return message
    
    def broadcastMessage(message_id):
        message = Message.objects.get(pk=message_id)
        message.broadcast = True
        message.save()
        return message

    def getMessages(lecture_id, is_student):
        messages = Message.objects.filter(lecture_id=lecture_id).order_by('time')
        messages = messages.values('message_id', 'lecture_id', 'auth_user_id', 'message', 'time', 'reply_message', 'blocked', 'auth_user__first_name', 'auth_user__last_name', 'hidden', 'broadcast')
        
        if is_student:
            messages = messages.exclude(blocked=True)

        return messages