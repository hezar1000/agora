from .settings import *


# PROD DEBUG = False
# PROD TIME_ZONE = "America/Vancouver"

# PROD STATIC_ROOT = '/var/agora/static'
# PROD MEDIA_ROOT = '/var/agora/uploads'

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    # PROD     '%IP%',
    "agora.students.cs.ubc.ca",
]

# SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# SECURE_SSL_REDIRECT = True
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True

# PROD LOGGING['handlers']['default_log']['filename'] = '/var/agora/log/django.log'
# PROD LOGGING['handlers']['events_log']['filename'] = '/var/agora/log/events.log'
# PROD LOGGING['handlers']['debug_log']['filename'] = '/var/agora/log/debug.log'
# PROD LOGGING['handlers']['requests_log']['filename'] = '/var/agora/log/requests.log'

# PROD DATABASES = {
# PROD     'default': {
# PROD         'ENGINE': 'django.db.backends.postgresql',
# PROD         'NAME': 'agora_db',
# PROD         'USER': 'agora',
# PROD         'PASSWORD': '%PASS%',
# PROD     },
# PROD }
