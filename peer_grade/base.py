import statistics, random
from django.db.models import Q
from django.conf import settings
import csv, io


from peer_course.base import CourseBase
from peer_course.models import CourseMember


from .choices import CLOSED, RESOLVED


class GradeBaseMain(object):

    def upload_grading_items(csv_file, cid):  # format: gradee - week - grade type (peer review or participation?) - grade - max grade- grading_method (TA or what?) - comments
        count=0
        decoded_file = csv_file.read().decode('utf-8-sig')
        io_string = io.StringIO(decoded_file)
        for row in csv.DictReader(io_string):
            gradee = CourseMember._default_manager.get(user__username=row['gradee'], course_id = cid)
            week = row['week']
            grade_type = row['grade type']  # peer review or participation ... 
            grade = row['grade']
            max_grade = row['max grade']
            grading_method = row['grading method'] #TA or peer or ?
            comments = row['comments']
            grading_items = GradingItem._default_manager.filter(gradee  = gradee, grade_type = grade_type, grading_period = week)
            if grading_items.exists():
                grading_item = grading_items[0]
                grading_item.grade = grade
                grading_item.max_grade = max_grade
                grading_item.grading_method = grading_method
                grading_item.comments = comments
                grading_item.save()
            else:
                GradingItem._default_manager.create(
                    gradee  = gradee,
                    grading_period = week,
                    grade_type = grade_type,
                    grade = grade,
                    max_grade = max_grade,
                    grading_method = grading_method,
                    comments = comments
                )
            count += 1
        return count
