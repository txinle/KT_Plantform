import os
from django.core.wsgi import get_wsgi_application
from config.capture_timing import WSGICaptureTiming
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = WSGICaptureTiming(get_wsgi_application())
