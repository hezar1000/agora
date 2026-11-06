# peer_account/forms.py
from django import forms
from django.forms import ModelForm
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.password_validation import validate_password
from PIL import Image, UnidentifiedImageError
import re
from .models import Profile

class UserForm(ModelForm):
    class Meta:
        model = User
        fields = ["username", "password", "email", "first_name", "last_name"]

    def clean_email(self):
        data = self.cleaned_data["email"].strip()
        if re.match(r"(^[a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.){1,}[a-zA-Z0-9-.]+$", data) is None:
            raise forms.ValidationError("Email Address Required")
        # One account per email address. Django's password reset mails every
        # account matching the address, so duplicates leave people holding
        # several reset links for different usernames with no way to tell
        # which is which.
        if User.objects.filter(email__iexact=data).exists():
            raise forms.ValidationError(
                "An account with this email already exists. Log in with it, or use "
                "\"Forgotten password? Reset here\" on the login page."
            )
        return data

    def clean_username(self):
        data = self.cleaned_data["username"]
        if re.match("^[a-zA-Z0-9_.]{2,16}$", data) is None:
            raise forms.ValidationError("Student/Employee ID is not correct")
        # Usernames are case-sensitive at login, so "jsmith" and "Jsmith"
        # would be two accounts nobody can tell apart. Refuse the second one
        # rather than relying on the model's case-sensitive uniqueness.
        if User.objects.filter(username__iexact=data).exists():
            raise forms.ValidationError("A user with that username already exists.")
        return data

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password


class UserEditForm(ModelForm):
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name"]

    def __init__(self, *args, **kwargs):
        self.can_edit_identity = kwargs.pop("can_edit_identity", False)
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({"class": "form-control"})

        if self.instance.is_staff:
            self.fields["username"].label = "User ID"
            self.fields["username"].help_text = ""
        else:
            self.fields["username"].label = "Student ID"
            if not self.can_edit_identity:
                self.fields["username"].disabled = True
                self.fields["username"].help_text = "Please ask a site administrator to revise this identity."

        # The registrar's name, and not this person's to change: the SAML
        # assertion consumer rewrites first_name and last_name from UBC on
        # every single sign-in, so an editable box here promised something it
        # could not keep. The edit survived until the next lecture and then
        # reverted -- in front of the class. A preferred name lives on the
        # profile instead, where nothing overwrites it.
        if not self.can_edit_identity:
            for name in ("first_name", "last_name"):
                self.fields[name].disabled = True
            self.fields["first_name"].help_text = (
                "As UBC has it. To change the name your instructor sees, "
                "set a preferred name below."
            )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["photo", "preferred_first_name", "preferred_last_name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({"class": "form-control"})
        self.fields["preferred_first_name"].help_text = (
            "The name you are called. Leave both blank to use the name UBC "
            "has on file."
        )
        self.fields["preferred_last_name"].help_text = (
            "This is what your instructor sees when they call on you, and "
            "what appears in the class list. It does not change your student "
            "number or your CWL."
        )

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if not photo:
            return photo
        if photo.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Profile photos must be 5 MB or smaller.")
        try:
            image = Image.open(photo)
            image.verify()
            photo.seek(0)
            image = Image.open(photo)
            if image.format not in {"JPEG", "PNG", "GIF", "WEBP"}:
                raise forms.ValidationError("Use a JPEG, PNG, GIF, or WebP image.")
            if image.width > 4096 or image.height > 4096:
                raise forms.ValidationError("Profile photos may be at most 4096×4096 pixels.")
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
            raise forms.ValidationError("The uploaded file is not a valid image.")
        finally:
            photo.seek(0)
        return photo


class HostPasswordResetForm(PasswordResetForm):
    """Build the reset link from the host that served the request.

    Django's default takes the domain from the Sites table, whose shipped row
    is ``example.com``. Production ran with that row for weeks, so every reset
    email that did arrive linked to example.com. ALLOWED_HOSTS already vets
    the Host header, so the request is the one domain that cannot be stale.
    """

    def save(self, *args, request=None, domain_override=None, **kwargs):
        if domain_override is None and request is not None:
            domain_override = request.get_host()
        return super().save(*args, request=request, domain_override=domain_override, **kwargs)
