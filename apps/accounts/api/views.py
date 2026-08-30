from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Course

from .serializers import CourseContextSerializer


class CourseContextView(APIView):
    @extend_schema(responses=CourseContextSerializer)
    def get(self, request, course_id):
        course = get_object_or_404(Course, pk=course_id)
        if not course.can_manage(request.user):
            raise NotFound()
        return Response(CourseContextSerializer(course).data)
