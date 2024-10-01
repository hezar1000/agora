from .settings import *


# PROD DEBUG = False
# PROD TIME_ZONE = "America/Vancouver"

# PROD STATIC_ROOT = '/var/mta/static'
# PROD MEDIA_ROOT = '/var/mta/uploads'

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    # PROD     '%IP%',
    "mta.students.cs.ubc.ca",
]

# SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# SECURE_SSL_REDIRECT = True
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True

# PROD LOGGING['handlers']['default_log']['filename'] = '/var/mta/log/django.log'
# PROD LOGGING['handlers']['events_log']['filename'] = '/var/mta/log/events.log'
# PROD LOGGING['handlers']['debug_log']['filename'] = '/var/mta/log/debug.log'
# PROD LOGGING['handlers']['requests_log']['filename'] = '/var/mta/log/requests.log'

# PROD DATABASES = {
# PROD     'default': {
# PROD         'ENGINE': 'django.db.backends.postgresql',
# PROD         'NAME': 'mta_db',
# PROD         'USER': 'mta',
# PROD         'PASSWORD': '%PASS%',
# PROD     },
# PROD }
