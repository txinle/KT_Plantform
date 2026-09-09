from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.course_access import accessible_courses, require_course_manager, require_writable_course
from apps.accounts.models import DataPurgeRequest, User
from apps.analytics.research_deletion import ARTIFACT_CLEANUP_SCOPE
from apps.api.exceptions import ApiConflict
from apps.api.permissions import RolePermission

from ..research_deletion import DeletionConflict, deletion_preview, execute_deletion


class DeletionClassSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    session_count = serializers.IntegerField()


class DeletionExperimentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    class_count = serializers.IntegerField()
    session_count = serializers.IntegerField()
    class_session_count = serializers.IntegerField()
    export_count = serializers.IntegerField()
    classes = DeletionClassSerializer(many=True)
    detail_url = serializers.CharField()


class DeletionCountsSerializer(serializers.Serializer):
    class_count = serializers.IntegerField()
    session_count = serializers.IntegerField()
    export_count = serializers.IntegerField()
    member_count = serializers.IntegerField()


class ResearchDeletionPreviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["delete_experiment", "remove_class_experiment", "delete_class"])
    object_id = serializers.IntegerField()
    object_name = serializers.CharField()
    allowed = serializers.BooleanField()
    blocked_reason = serializers.CharField(allow_blank=True)
    experiments = DeletionExperimentSerializer(many=True)
    counts = DeletionCountsSerializer()
    confirmation_token = serializers.CharField()


class ResearchDeletionInputSerializer(serializers.Serializer):
    confirmation = serializers.ChoiceField(choices=["delete"])
    confirmation_token = serializers.CharField()


class ResearchDeletionResultSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()
    cleanup_pending = serializers.BooleanField()
    cleanup_id = serializers.UUIDField(allow_null=True)


class ResearchDeletionCleanupSerializer(serializers.Serializer):
    cleanup_pending = serializers.BooleanField()
    status = serializers.ChoiceField(choices=["pending", "completed"])
    error = serializers.CharField(allow_blank=True)


class ResearchDeletionView(APIView):
    permission_classes = [RolePermission]
    allowed_roles = (User.Role.TEACHER, User.Role.ADMIN)
    action = "delete_experiment"

    def context(self, request, course_id, experiment_id=None, class_id=None):
        course = get_object_or_404(accessible_courses(request.user), pk=course_id)
        require_course_manager(course, request.user)
        experiment = get_object_or_404(course.experiments, pk=experiment_id) if experiment_id is not None else None
        class_group = get_object_or_404(course.classes, pk=class_id) if class_id is not None else None
        return {"course": course, "action": self.action, "experiment": experiment, "class_group": class_group}

    @extend_schema(responses=ResearchDeletionPreviewSerializer)
    def get(self, request, **kwargs):
        data = deletion_preview(actor=request.user, **self.context(request, **kwargs))
        return Response(ResearchDeletionPreviewSerializer(data).data)

    @extend_schema(request=ResearchDeletionInputSerializer, responses=ResearchDeletionResultSerializer)
    def post(self, request, **kwargs):
        context = self.context(request, **kwargs)
        require_writable_course(context["course"])
        serializer = ResearchDeletionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            data = execute_deletion(request=request, confirmation_token=serializer.validated_data["confirmation_token"], **context)
        except DeletionConflict as exc:
            raise ApiConflict("；".join(exc.messages), code="DELETION_SCOPE_CHANGED") from exc
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        return Response(ResearchDeletionResultSerializer(data).data)


class TeacherExperimentDeletionView(ResearchDeletionView):
    action = "delete_experiment"


class TeacherClassExperimentRemoveView(ResearchDeletionView):
    action = "remove_class_experiment"


class TeacherClassDeletionView(ResearchDeletionView):
    action = "delete_class"


class TeacherDeletionCleanupView(APIView):
    permission_classes = [RolePermission]
    allowed_roles = (User.Role.TEACHER, User.Role.ADMIN)

    @extend_schema(responses=ResearchDeletionCleanupSerializer)
    def get(self, request, course_id, cleanup_id):
        course = get_object_or_404(accessible_courses(request.user), pk=course_id)
        require_course_manager(course, request.user)
        cleanup = get_object_or_404(DataPurgeRequest, purge_request_id=cleanup_id,
            scope_type=ARTIFACT_CLEANUP_SCOPE, scope_definition__course_id=course_id)
        pending = cleanup.status != DataPurgeRequest.Status.EXECUTED
        return Response(ResearchDeletionCleanupSerializer({"cleanup_pending": pending,
            "status": "pending" if pending else "completed", "error": cleanup.result_counts.get("error", "")}).data)
