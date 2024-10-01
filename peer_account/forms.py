from django import forms
from django.forms import ModelForm
from django.contrib.auth.models import User
from django.forms.models import model_to_dict

import re


class UserForm(ModelForm):
    class Meta:
        model = User
        fields = ["username", "password", "email", "first_name", "last_name"]

    def clean_email(self):
        data = self.cleaned_data["email"]
        # if re.match("^([a-zA-Z0-9_\-\.]+)@((ugrad\.cs|alumni|cs){,1}\.)*ubc\.ca$", data) is None:
        # 	raise forms.ValidationError("UBC Email Address Required")
        if (
            re.match(r"(^[a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.){1,}[a-zA-Z0-9-.]+$", data)
            is None
        ):
            raise forms.ValidationError("Email Address Required")
        return data

    def clean_username(self):
        data = self.cleaned_data["username"]
        if re.match("^[a-zA-Z0-9_.]{2,16}$", data) is None:
            raise forms.ValidationError("Student/Employee ID is not correct")
        return data


class UserEditForm(ModelForm):
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name"]

    def __init__(self, *args, **kwargs):
        self.is_superuser = kwargs.pop("is_superuser")
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({"class": "form-control"})

        if self.instance.is_staff:
            self.fields["username"].label = "User ID"
            self.fields["username"].help_text = ""
        else:
            self.fields["username"].label = "Student ID"
            if not self.is_superuser:
                self.fields["username"].disabled = True
                self.fields[
                    "username"
                ].help_text = "Please ask your instructor to revise your Student ID."
