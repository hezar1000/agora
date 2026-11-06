from django import forms
from django.forms import (
    BaseInlineFormSet,
    DateInput,
    ModelForm,
    inlineformset_factory,
    modelformset_factory,
)
from django.db.models import Q
from datetime import datetime, date, time, timedelta
from django.utils import timezone
from multiselectfield import MultiSelectFormField
from .models import DAYS_OF_WEEK


from . import passphrase
from .models import Course, CourseSession


#: A three-word code from peer_course.passphrase is 15 characters at its
#: shortest, so this rejects a one-word code without rejecting ours.
MIN_STUDENT_CODE_LENGTH = 12


class CourseForm(ModelForm):
    """
    Edits the main Course fields
    """
    class Meta:
        model = Course
        fields = [
            "displayname",
            "browsable",
            "archived",
            "auto_start",
            "default_session_duration",
            "course_timezone",
            "weight_exponent",
            "export_prompt_at_end",
            "manually_started",
            "instructor_code",
            "tascode",
            "included_lists",
            "stucode",
            "show_participation_grade",
            "allow_multiple_queues",
            "classroom_microphone",
            "lecture_guests",
            "spoken_weight",
        ]

    # Fields whose markup is hand-written further down the settings page.
    # `included_lists` is drawn beside the scheme it belongs to, labelled with
    # each queue's own name rather than the colour behind it.
    CUSTOM_FIELDS = (
        "included_lists",
    )

    # Presentation order, which is not declaration order. The first three
    # groups are the course basics every instructor expects first; everything
    # after them is specific to participation.
    FIELDSETS = (
        (
            "course",
            "Course",
            "The name students see, and the timezone every date and deadline "
            "is shown in.",
            ("displayname", "course_timezone"),
        ),
        (
            "visibility",
            "Visibility",
            "Whether students can reach this course.",
            ("browsable", "archived"),
        ),
        (
            "enrolment",
            "Enrolment codes",
            "People join by entering one of these. Each must be at least 20 "
            "characters and unique across every course.",
            ("instructor_code", "tascode", "stucode"),
        ),
        (
            # Not "lectures": the term's schedule further down the page already
            # owns that id and that word, and two nav links reading "Lectures"
            # tell an instructor nothing about which one they want.
            "lecture-settings",
            "Running a lecture",
            "How a lecture starts, and how long it stays open.",
            (
                "auto_start",
                "manually_started",
                "default_session_duration",
                "export_prompt_at_end",
                "allow_multiple_queues",
                "classroom_microphone",
                "lecture_guests",
            ),
        ),
        (
            "calling",
            "Being called on",
            "How Agora chooses between the hands that are up.",
            ("spoken_weight", "weight_exponent"),
        ),
        (
            "students",
            "What students see",
            "",
            ("show_participation_grade",),
        ),
    )

    def fieldsets(self):
        """The bound fields, grouped for display."""
        for slug, title, description, names in self.FIELDSETS:
            yield {
                "id": slug,
                "title": title,
                "description": description,
                "fields": [self[name] for name in names if name in self.fields],
            }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance", None)
        self.courseid = instance.id if instance else None
        self._initial_codes = {
            "instructor_code": getattr(instance, "instructor_code", None),
            "tascode": getattr(instance, "tascode", None),
            "stucode": getattr(instance, "stucode", None),
        }
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                # Bootstrap 3 stretches .form-control to 100% width, so a
                # checkbox wearing it becomes a large empty box adrift from
                # its label. Checkboxes are styled by their .checkbox wrapper.
                continue
            field.widget.attrs.update({"class": "form-control"})

        # These explanations used to exist only as hover tooltips, so they were
        # invisible to anyone who did not happen to point at the right icon.
        for name, text in {
            "displayname": "The name students see for this course.",
            "browsable": (
                "Students can open the course and raise their hand. Turn it "
                "off to close the course without deleting anything."
            ),
            # Course.archived is written by the Archive action and read nowhere
            # in this codebase. The previous wording described behaviour that
            # does not exist.
            "archived": (
                "Recorded on the course. Nothing in Agora currently changes "
                "behaviour based on it."
            ),
            "default_session_duration": "Length in minutes of a session that does not set its own.",
        }.items():
            if not self.fields[name].help_text:
                self.fields[name].help_text = text

    def clean_instructor_code(self):
        """
        Validate that the instructor code isn't used by another course.
        """
        instructor_code = self.cleaned_data["instructor_code"]
        # Staff codes are pasted into an email once, never read out, and
        # typing this one makes you an instructor of the course -- so it stays long
        # and random. Only the student code is made of words.
        if not instructor_code or len(instructor_code) < 20:
            raise forms.ValidationError(
                "Use a randomly generated code of at least 20 characters. "
                "Only the student code can be words."
            )
        if self.courseid:
            course_found = Course.objects.filter(
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
        """
        Validate that the student code isn't used by another course.
        """
        stucode = self.cleaned_data["stucode"]
        # This is the one a class types off a slide, so it does not have to
        # be twenty random characters -- three words is plenty against ten
        # enrolment attempts per account every five minutes, and enrolling
        # gets you no more than the student role in a course already open to
        # students. New courses are given a code like "copper-lantern-thistle".
        if not stucode or len(stucode.strip()) < MIN_STUDENT_CODE_LENGTH:
            raise forms.ValidationError(
                "Use at least %d characters -- three or four words joined by "
                "hyphens works well, for example \u201c%s\u201d."
                % (MIN_STUDENT_CODE_LENGTH, passphrase.generate())
            )
        if self.courseid:
            course_found = Course.objects.filter(
                ~Q(id=self.courseid),
                Q(stucode=stucode)
                | Q(tascode=stucode)
                | Q(instructor_code=stucode),
            )
            if course_found.exists():
                raise forms.ValidationError(
                    "You chose a student access code that has been used by another course. "
                    "Please enter a different student access code.",
                    code="invalid-stucode",
                )
        return stucode

    def clean_tascode(self):
        """
        Validate that the TA code isn't used by another course.
        """
        tascode = self.cleaned_data["tascode"]
        # Staff codes are pasted into an email once, never read out, and
        # typing this one makes you a TA of the course -- so it stays long
        # and random. Only the student code is made of words.
        if not tascode or len(tascode) < 20:
            raise forms.ValidationError(
                "Use a randomly generated code of at least 20 characters. "
                "Only the student code can be words."
            )
        if self.courseid:
            course_found = Course.objects.filter(
                ~Q(id=self.courseid),
                Q(tascode=tascode)
                | Q(stucode=tascode)
                | Q(instructor_code=tascode),
            )
            if course_found.exists():
                raise forms.ValidationError(
                    "You chose a TA access code that has been used by another course. "
                    "Please enter a different TA acceess code.",
                    code="invalid-tascode",
                )
        return tascode

    def save(self, commit=True):
        course = super().save(commit=False)
        expiry = timezone.now() + timedelta(days=180)
        expiry_fields = {
            "instructor_code": "instructor_code_expires_at",
            "tascode": "ta_code_expires_at",
            "stucode": "student_code_expires_at",
        }
        for code_field, expiry_field in expiry_fields.items():
            if self.cleaned_data.get(code_field) != self._initial_codes.get(code_field):
                setattr(course, expiry_field, expiry)
        if commit:
            course.save()
            self.save_m2m()
        return course



class CourseSessionForm(ModelForm):
    is_template = forms.BooleanField(required=False)

    active_days = MultiSelectFormField(
        choices=DAYS_OF_WEEK,
        required=False
    )

    class Meta:
        model = CourseSession
        fields = ["name",
                  "start_time_pst",
                  "is_template",
                  "end_time_pst",
                  "active_days",
                  "session_date",
                  "session_end_date",
                  "cancel_next_session"]
        widgets = {
            'session_date': DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                # Bootstrap 3 stretches .form-control to 100% width, so a
                # checkbox wearing it becomes a large empty box adrift from
                # its label. Checkboxes are styled by their .checkbox wrapper.
                continue
            field.widget.attrs.update({"class": "form-control"})

    def clean_active_days(self):
        data = self.cleaned_data.get("active_days")
        if hasattr(data, '__iter__') and not isinstance(data, str):
            joined = ",".join([str(item).strip() for item in data])
            return joined
        return data

    def clean_session_date(self):
        data = self.cleaned_data.get("session_date")
        if not data:
            return None
        if isinstance(data, date):
            return data
        try:
            parsed_date = datetime.strptime(data, "%Y-%m-%d").date()
        except ValueError:
            raise forms.ValidationError("Invalid date format. Use YYYY-MM-DD.")
        return parsed_date
    
    def clean_session_end_date(self):
        data = self.cleaned_data.get("session_end_date")
        if not data:
            return None
        if isinstance(data, date):
            return data
        try:
            parsed_date = datetime.strptime(data, "%Y-%m-%d").date()
        except ValueError:
            raise forms.ValidationError("Invalid date format. Use YYYY-MM-DD.")
        return parsed_date

    def clean_start_time_pst(self):
        data = self.cleaned_data.get("start_time_pst")
        if not data:
            return None
        if isinstance(data, time):
            return data
        try:
            parsed_time = datetime.strptime(data, "%H:%M").time()
        except ValueError:
            raise forms.ValidationError("Invalid time format. Use HH:MM.")
        return parsed_time

    def clean_end_time_pst(self):
        data = self.cleaned_data.get("end_time_pst")
        if not data:
            return None
        if isinstance(data, time):
            return data
        try:
            parsed_time = datetime.strptime(data, "%H:%M").time()
        except ValueError:
            raise forms.ValidationError("Invalid time format. Use HH:MM.")
        return parsed_time
    
    def clean(self):
        """Check each ordering independently, against the field it concerns.

        Previously both checks were guarded by all four fields being present,
        so a weekly pattern -- which carries times but often no dates -- was
        never time-checked at all, and could be saved ending before it started.

        The errors also went to `__all__`, where the settings page never
        rendered them: the form appeared to save and silently did not.
        """
        clean_data = super().clean()
        start_time = clean_data.get("start_time_pst")
        end_time = clean_data.get("end_time_pst")
        session_date = clean_data.get("session_date")
        session_end_date = clean_data.get("session_end_date")

        if session_date and session_end_date and session_date > session_end_date:
            self.add_error(
                "session_end_date",
                "The last date must be on or after the first date.",
            )
        if start_time and end_time and start_time >= end_time:
            self.add_error(
                "end_time_pst", "The end time must be after the start time."
            )
        return clean_data
    

class CustomCourseSessionFormSet(BaseInlineFormSet):
    def save(self, commit=True):
        for form in self.forms:
            if form.cleaned_data.get("DELETE", False):
                continue
            instance = form.instance
            if isinstance(instance.active_days, (list, tuple)):
                instance.active_days = ",".join(instance.active_days)
        return super().save(commit)

CourseSessionFormSet = inlineformset_factory(
    parent_model=Course,
    model=CourseSession,
    form=CourseSessionForm,
    formset=CustomCourseSessionFormSet,  
    extra=1,       
    can_delete=True
)


class LectureForm(ModelForm):
    """One lecture that actually happened, as opposed to the weekly pattern.

    The settings page has only ever edited templates, so an instructor could
    describe "Tuesdays at ten" but never say anything about last Tuesday --
    which is exactly where a name and an exclusion belong.
    """

    class Meta:
        model = CourseSession
        fields = ["name", "counts_for_participation", "cancelled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update(
            {"class": "form-control", "placeholder": self.session_label()}
        )
        self.fields["name"].label = "Name"
        self.fields["counts_for_participation"].label = "Counts toward the grade"
        self.fields["cancelled"].label = "No lecture"

    def clean(self):
        cleaned = super().clean()
        # A day with no lecture cannot also be a lecture that counts. Saying so
        # here means the two checkboxes cannot be left contradicting each other.
        if cleaned.get("cancelled"):
            cleaned["counts_for_participation"] = False
        return cleaned

    def session_label(self):
        instance = self.instance
        if instance and instance.pk and instance.session_date:
            return instance.session_date.strftime("%-d %b %Y")
        return "This lecture"


LectureFormSet = modelformset_factory(
    CourseSession, form=LectureForm, extra=0, can_delete=False
)
