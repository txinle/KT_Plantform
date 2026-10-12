from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from .course_access import get_accessible_course

def _class_drilldown_scope(request, course_pk, class_pk):
    if not settings.CLASS_CENTER_ENABLED:
        raise Http404
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    return course, class_group
