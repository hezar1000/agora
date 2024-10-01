"""agora URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.10/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.conf.urls import url, include
    2. Add a URL to urlpatterns:  url(r'^blog/', include('blog.urls'))
"""
from django.conf.urls import include, url
from django.urls import path
from django.contrib import admin


from django.conf.urls.static import static
from django.conf import settings
from django.views.static import serve

import django_saml2_pro_auth.urls as saml_urls

urlpatterns = [
    url(r"^static/(?P<path>.*)$", serve, settings.STATIC_DICT),
    url(r"^", include(("peer_home.urls", "peer_home"), namespace="home")),
    url(
        r"^account/",
        include(("peer_account.urls", "peer_account"), namespace="account"),
    ),
    url(r"^course/", include(("peer_course.urls", "peer_course"), namespace="course")),
    url(r"^grade/", include(("peer_grade.urls", "peer_grade"), namespace="grade")),
    path(r"admin/", admin.site.urls),
    url(r"^comments/", include(("django_comments.urls", "django_comments"))),
    url(r"^nested_admin/", include(("nested_admin.urls", "nested_admin"))),
    url(r"^", include((saml_urls, "saml"), namespace="saml")),
]

# Makes serving file work in development
if settings.DEBUG is True:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
