import os
import sys
import django

sys.path.append('/var/www/vhosts/agora')
sys.path.append('/var/www/vhosts/agora/venv/lib/python3.8/site-packages')
sys.path.append('/var/www/vhosts/agora/venv/lib/python3.5/site-packages')

from channels.routing import get_default_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "agora.development_settings")
django.setup()
application = get_default_application()
