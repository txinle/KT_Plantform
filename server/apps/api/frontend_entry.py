"""Business page names for URL generation; HTML belongs to the client gateway."""
import json
from collections import defaultdict

from django.conf import settings
from django.http import HttpResponseNotFound
from django.urls import include, path


def frontend_page(request=None, **kwargs):
    return HttpResponseNotFound()


def page_urlpatterns():
    catalog = json.loads((settings.REPOSITORY_DIR / 'contracts/frontend-routes.json').read_text(encoding='utf-8'))
    namespaces = defaultdict(list)
    patterns = []
    for route in catalog['routes']:
        namespace, separator, name = route['name'].partition(':')
        if separator:
            namespaces[namespace].append(path(route['path'], frontend_page, name=name))
        else:
            patterns.append(path(route['path'], frontend_page, name=namespace))
    for namespace, entries in namespaces.items():
        patterns.append(path('', include((entries, namespace), namespace=namespace)))
    return patterns
