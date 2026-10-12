from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ResearchStudy, User
from apps.api.permissions import RolePermission
from apps.audit.services import log_action
from ..services import create_export_snapshot, create_export_job, ExportContractError
from .serializers import DataCenterExportJobMutationSerializer
from .views import _export_job_payload


class StudyExportRequestSerializer(serializers.Serializer):
    package = serializers.CharField(required=False, default="longitudinal", max_length=32)


class TeacherResearchStudyExportView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    @extend_schema(operation_id="teacher_research_study_export_create",
        request=StudyExportRequestSerializer,
        responses={202: DataCenterExportJobMutationSerializer,
                   **{code: {'type': 'object'} for code in (400, 401, 403, 404)}},
        parameters=[OpenApiParameter('X-CSRFToken', str, OpenApiParameter.HEADER,
            description='Django CSRF token, required for unsafe methods.')])
    def post(self, request, study_id):
        study = get_object_or_404(ResearchStudy, pk=study_id)
        if not (request.user.is_superuser or request.user.role == User.Role.ADMIN or study.owner_id == request.user.pk):
            raise NotFound()
        link = study.course_links.select_related("course").first()
        if link is None:
            raise ValidationError("研究项目尚未关联课程")
        serializer = StudyExportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        package = serializer.validated_data["package"]
        try:
            snapshot = create_export_snapshot(course=link.course, research_study=study, created_by=request.user)
            job = create_export_job(course=link.course, research_study=study, snapshot=snapshot,
                created_by=request.user, package=package)
        except ExportContractError as error:
            raise ValidationError(str(error)) from error
        log_action(request, "analytics.identified_export_job_create", link.course,
            {"job_id": str(job.pk), "package": package, "research_study_id": study.pk})
        return Response(DataCenterExportJobMutationSerializer(_export_job_payload(job)).data, status=202)
