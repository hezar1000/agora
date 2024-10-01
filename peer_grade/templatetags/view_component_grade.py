from django import template
from django.conf import settings

register = template.Library()


@register.inclusion_tag("peer_grade/tags/view-component-grade.html")
def view_component_grade(component, can_edit):
    grading = component.final_grading()
    rubric = component.question.rubric
    return {
        "grade": grading["grade"],
        "grade_available": grading["grade"] is not None,
        "method": grading["method"],
        # editing is for staff and only works if component-wise grading is being used
        "can_edit": can_edit and getattr(settings, "COMPONENTWISE_GRADE", False),
        "component_id": component.id,
        "can_see_grade": can_edit or component.submission.ta_deadline_passed(),
        "max_grade": rubric.max_total_grade()
        if rubric is not None
        else "No rubric assigned",
    }
