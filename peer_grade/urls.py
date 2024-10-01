from django.conf.urls import url

from .views import GradeViews

urlpatterns = [
    # set manual grade by the instructor/TA
    url(r"^gradebook/$", GradeViews.show_grade_book, name="gradebook"),
    url(r"^upload_grading_items/$", GradeViews.upload_grading_items, name="upload_grading_items"),
    # url(
    #     r"^gradebook/export/$",
    #     GradeViews.export_course_grades,
    #     name="export_course",
    # ),
]
