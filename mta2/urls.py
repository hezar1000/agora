"""mta2 URL Configuration

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
from django.urls import include, re_path
from django.urls import path
from django.contrib import admin


from django.conf.urls.static import static
from django.conf import settings
from mta2 import health
from mta2 import views as mta2_views

urlpatterns = [
    path('health/live/', health.live, name='health-live'),
    path('health/ready/', health.ready, name='health-ready'),
    path('sso/custom_saml/login/<str:provider>/', mta2_views.custom_saml_login, name='custom_saml_login'),
    path('sso/saml/<str:provider>/acs/', mta2_views.custom_saml_acs, name='custom_saml_acs'),
    path('sso/saml/', mta2_views.legacy_saml_root, name='legacy_saml_root'),
    re_path(r"^", include(("peer_home.urls", "peer_home"), namespace="home")),
    re_path(
        r"^account/",
        include(("peer_account.urls", "peer_account"), namespace="account"),
    ),
    re_path(r"^course/", include(("peer_course.urls", "peer_course"), namespace="course")),
    # The admin has its own login form wired straight to ModelBackend, so a
    # second factor demanded only by AuthViews.user_login would be walked
    # around by going to /admin/. RequireSecondFactorMiddleware gates every
    # admin page except its login and logout, which closes that without
    # swapping the admin site class -- a swap would be decided at import
    # time and would go on demanding a token after MFA_ENFORCED was turned
    # off, which is the one moment somebody needs the admin to still work.
    #
    # Anchored: an unanchored "admin/" is matched with re.search, so
    # /anything/admin/ reached the admin too.
    re_path(r"^admin/", admin.site.urls),
]

if settings.DEBUG is True:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
