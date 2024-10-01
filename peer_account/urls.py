from django.conf.urls import url

from .views import AuthViews

urlpatterns = [
    url(r"^signup/$", AuthViews.user_signup, name="signup"),
    url(r"^saml_login/$", AuthViews.saml_login, name="saml_login"),
    url(r"^login/$", AuthViews.user_login, name="login"),
    url(r"^saml_logout/$", AuthViews.saml_logout, name="saml_logout"),
    url(r"^logout/$", AuthViews.user_logout, name="logout"),
    url(r"^(?P<uid>[0-9]+)/view/$", AuthViews.user_view, name="view"),
    url(r"^(?P<uid>[0-9]+)/edit/$", AuthViews.user_edit, name="edit"),
    url(
        r"^api/(?P<cid>[0-9]+)/unregistered/",
        AuthViews.get_unenrolled_users,
        name="get_unenrolled_users",
    ),
]
