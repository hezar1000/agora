from django.urls import include, re_path
from django.contrib.auth import views as auth_views
from django.conf import settings
from .forms import HostPasswordResetForm
from .views import AuthViews, MFAViews, PasswordChangeView

urlpatterns = [
    re_path(r"^signup/$", AuthViews.user_signup, name="signup"),
    re_path(r"^login/$", AuthViews.user_login, name="login"),
    re_path(r"^update_name/$", AuthViews.update_name, name="update_name"),
    re_path(r"^logout/$", AuthViews.user_logout, name="logout"),
    re_path(r"^mfa/setup/$", MFAViews.setup, name="mfa_setup"),
    re_path(r"^mfa/verify/$", MFAViews.verify, name="mfa_verify"),
    re_path(r"^about/$", AuthViews.about, name="about"),
    re_path(r"^password_change/$", PasswordChangeView.as_view(), name="password_change"),
    re_path(r"^(?P<uid>[0-9]+)/view/$", AuthViews.user_view, name="view"),
    re_path(r"^(?P<uid>[0-9]+)/protected_photo/$", AuthViews.protected_photo, name="protected_photo"),
    re_path(r"^(?P<uid>[0-9]+)/edit/$", AuthViews.user_edit, name="edit"),
    re_path(r"^api/(?P<cid>[0-9]+)/unregistered/", AuthViews.get_unenrolled_users, name="get_unenrolled_users"),
    re_path(
        r"^password_reset/$",
        auth_views.PasswordResetView.as_view(
            form_class=HostPasswordResetForm,
            template_name="account/password_reset_form.html",
            email_template_name="account/password_reset_email.txt",
            subject_template_name="account/password_reset_subject.txt",
            success_url="/account/password_reset/done/",
            # From DEFAULT_FROM_EMAIL, like every other message the site
            # sends; a hard-coded sender here silently ignored the deployment
            # setting that the SMTP relay is authorised for.
        ),
        name="password_reset",
    ),
    re_path(
        r"^password_reset/done/$",
        auth_views.PasswordResetDoneView.as_view(
            template_name="account/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    re_path(
        r"^reset/(?P<uidb64>[0-9A-Za-z_\-]+)/(?P<token>.+)/$",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="account/password_reset_confirm.html",
            success_url="/account/reset/done/",
        ),
        name="password_reset_confirm",
    ),
    re_path(
        r"^reset/done/$",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="account/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]
