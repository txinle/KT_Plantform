from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.course_access import accessible_courses
from apps.accounts.models import User
from apps.api.exceptions import ApiConflict
from apps.api.permissions import RolePermission

from ..services import archive_experiment
from .serializers import TeacherExperimentSerializer


class TeacherExperimentArchiveView(APIView):
    permission_classes = [RolePermission]
    allowed_roles = (User.Role.TEACHER, User.Role.ADMIN)

    @extend_schema(request=None, responses=TeacherExperimentSerializer)
    def post(self, request, course_id, experiment_id):
        course = get_object_or_404(accessible_courses(request.user), pk=course_id)
        try:
            experiment = archive_experiment(course=course, experiment_id=experiment_id, actor=request.user)
        except DjangoValidationError as error:
            raise ApiConflict("；".join(error.messages), code=error.code) from error
        return Response(TeacherExperimentSerializer(experiment, context={"request": request}).data)
