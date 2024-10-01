from django import forms
from django.forms import ModelForm
from django.db.models import Q

from .models import Course


class CourseForm(ModelForm):
    class Meta:
        model = Course
        fields = [
            "displayname",
            "browsable",
            "archived",
            "instructor_code",
            "tascode",
            "stucode",
            "points_upon_participation_in_green_list",
            "points_upon_participation_in_blue_list",
            "points_upon_participation_in_red_list",
            "points_upon_participation_in_yellow_list",
            "fraction_of_points_gained_upon_further_participations"
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.courseid = kwargs.pop("instance").id
        for field in self.fields:
            self.fields[field].widget.attrs.update({"class": "form-control"})

    def clean_instructor_code(self):
        instructor_code = self.cleaned_data["instructor_code"]
        course_found = Course._default_manager.filter(
            ~Q(id=self.courseid),
            Q(stucode=instructor_code)
            | Q(tascode=instructor_code)
            | Q(instructor_code=instructor_code),
        )
        if course_found.exists():
            raise forms.ValidationError(
                "You chose an instructor access code that has been used by another course. "
                "Please enter a different instructor access code.",
                code="invalid-instructor_code",
            )
        return instructor_code

    def clean_stucode(self):
        stucode = self.cleaned_data["stucode"]
        course_found = Course._default_manager.filter(
            ~Q(id=self.courseid),
            Q(stucode=stucode) | Q(tascode=stucode) | Q(instructor_code=stucode),
        )
        if course_found.exists():
            raise forms.ValidationError(
                "You chose a student access code that has been used by another course. "
                "Please enter a different student access code.",
                code="invalid-stucode",
            )
        return stucode

    def clean_tascode(self):
        tascode = self.cleaned_data["tascode"]
        course_found = Course._default_manager.filter(
            ~Q(id=self.courseid),
            Q(tascode=tascode) | Q(stucode=tascode) | Q(instructor_code=tascode),
        )
        if course_found.exists():
            raise forms.ValidationError(
                "You chose a TA access code that has been used by another course. "
                "Please enter a different TA acceess code.",
                code="invalid-tascode",
            )
        return tascode

class ImportStudentCIs(forms.Form):
    Update_supervisory_status= forms.BooleanField(required=False,initial=False,)
    Supervised_threshold= forms.FloatField(required= False)
    file = forms.FileField()

    def clean(self):
        cleaned_data = super(ImportStudentCIs, self).clean()
        update_supervised = cleaned_data.get("Update_supervisory_status")
        supervised_threshold = cleaned_data.get("Supervised_threshold")
        if update_supervised and supervised_threshold is None :
            raise forms.ValidationError("Supervised threshold is a required field when update supervisory status is checked.",
            code= "No-threshold"
            )
        return cleaned_data