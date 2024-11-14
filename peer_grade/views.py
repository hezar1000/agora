import csv
import logging

from django.shortcuts import get_object_or_404, HttpResponseRedirect, HttpResponse
from django.shortcuts import render as djangoRender
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError

from itertools import chain
import json

from peer_home.wrappers import render
from peer_course.base import *
from peer_course.models import CourseMember
from peer_course.decorators import chosen_course_required
from peer_home.popup_widgets import PopupUtils
from .models import GradingItem


from .forms import *
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt

# Create your views here.

eventLogger = logging.getLogger("agora.events")



class GradeViews:
    @staticmethod
    @login_required
    @chosen_course_required
    def show_grade_book(request):
        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)
        request.session["course_id"] = course.id
        coursemember = CourseBase.get_course_member(request.user, course.id)
        render_dict=dict()
        if coursemember.role == 'student':
            # with open('./vzF3THBgrUoGkqhA.json', 'r') as fp:
            #     data = json.load(fp)
            # render_dict["review_grades"] = data[coursemember.user.username]
            participation_grades = GradingItem.objects.filter(gradee = coursemember, grade_type = 'Participation').order_by('-grading_period')
            render_dict['participation_grades'] = participation_grades
            return render(request, "gradebook.html", render_dict) 
        
        if coursemember.role == 'instructor':
            pass

    @staticmethod
    @login_required
    @chosen_course_required
    def upload_grading_items(request):

        course = get_object_or_404(Course, pk=request.session["course_id"])
        render_dict = dict()


        CoursePermissions.require_course_staff(request.user, course.id)

        if request.method == 'POST':
            form = UploadGradingItems(request.POST, request.FILES)
            if form.is_valid():
                csv_file = request.FILES['file']
                # let's check if it is a csv file
                if not csv_file.name.endswith('.csv'):
                    messages.error(request, 'This is not a CSV file.')
                else:
                    count= GradeBaseMain.upload_grading_items(csv_file, course.id)
                    messages.success(request, "Successfully uploaded %d grades for students." % count)
                    # return HttpResponseRedirect("gradebook.html")

        
        form = UploadGradingItems()

        render_dict["form"] = form
        return render(request, "upload_grade_items.html", render_dict)

