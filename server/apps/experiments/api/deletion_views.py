from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.course_access import accessible_courses, require_course_manager, require_writable_course
from apps.accounts.models import DataPurgeRequest, User
from apps.analytics.research_deletion import ARTIFACT_CLEANUP_SCOPE, cleanup_state
from apps.api.exceptions import ApiConflict
from apps.api.permissions import RolePermission

from ..research_deletion import DeletionConflict, deletion_preview, execute_deletion
from ..deletion_operations import execute_registered_deletion, operation_payload, register_deletion_operation
from ..models import ResearchDeletionOperation


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
    coverage_run_count = serializers.IntegerField()
    coverage_session_count = serializers.IntegerField()


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
    operation_id = serializers.UUIDField(required=False)
    confirmation = serializers.ChoiceField(choices=["delete"])
    confirmation_token = serializers.CharField()


class ResearchDeletionResultSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    deleted = serializers.BooleanField()
    cleanup_pending = serializers.BooleanField()
    cleanup_id = serializers.UUIDField(allow_null=True)


class ResearchDeletionCleanupSerializer(serializers.Serializer):
    cleanup_pending = serializers.BooleanField()
    status = serializers.ChoiceField(choices=["pending", "completed"])
    error = serializers.CharField(allow_blank=True)
    state = serializers.ChoiceField(choices=['pending', 'running', 'completed', 'failed'])
    attempts = serializers.IntegerField(min_value=0)
    next_retry_at = serializers.DateTimeField(allow_null=True)
    retained_count = serializers.IntegerField(min_value=0)


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
        course = get_object_or_404(accessible_courses(request.user), pk=kwargs['course_id'])
        require_course_manager(course, request.user)
        serializer = ResearchDeletionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if 'operation_id' not in serializer.validated_data:
            raise ApiConflict('请刷新页面并重新预览确认删除范围。', code='DELETION_PREVIEW_REQUIRED')
        try:
            data = execute_registered_deletion(request=request, course=course, action=self.action,
                object_id=kwargs.get('experiment_id') or kwargs.get('class_id'), class_id=kwargs.get('class_id'),
                **{key: serializer.validated_data[key] for key in ('operation_id', 'confirmation_token')})
        except DeletionConflict as exc:
            raise ApiConflict("；".join(exc.messages), code=exc.code) from exc
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        return Response(ResearchDeletionResultSerializer(data).data)


class ResearchDeletionOperationInputSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=['delete_experiment', 'remove_class_experiment', 'delete_class'])
    object_id = serializers.IntegerField(min_value=1)
    class_id = serializers.IntegerField(min_value=1, allow_null=True)
    confirmation_token = serializers.CharField()

    def validate(self, attrs):
        if attrs['action'] == 'delete_class' and attrs['class_id'] not in (None, attrs['object_id']):
            raise serializers.ValidationError({'class_id': '班级与删除目标不一致。'})
        if attrs['action'] == 'remove_class_experiment' and attrs['class_id'] is None:
            raise serializers.ValidationError({'class_id': '请选择班级。'})
        if attrs['action'] == 'delete_experiment' and attrs['class_id'] is not None:
            raise serializers.ValidationError({'class_id': '删除整个实验不能限定班级。'})
        return attrs


class ResearchDeletionOperationSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    state = serializers.ChoiceField(choices=['pending', 'succeeded', 'rejected'])
    execute_before = serializers.DateTimeField()
    result = ResearchDeletionResultSerializer(allow_null=True)
    error_code = serializers.CharField(allow_blank=True)
    message = serializers.CharField(allow_blank=True)


class TeacherDeletionOperationCollectionView(APIView):
    permission_classes = [RolePermission]
    allowed_roles = (User.Role.TEACHER, User.Role.ADMIN)

    @extend_schema(request=ResearchDeletionOperationInputSerializer, responses={200: ResearchDeletionOperationSerializer, 201: ResearchDeletionOperationSerializer})
    def post(self, request, course_id):
        course = get_object_or_404(accessible_courses(request.user), pk=course_id)
        serializer = ResearchDeletionOperationInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            operation, created = register_deletion_operation(course=course, actor=request.user, **serializer.validated_data)
        except DeletionConflict as error:
            raise ApiConflict('；'.join(error.messages), code=error.code) from error
        return Response(ResearchDeletionOperationSerializer(operation_payload(operation)).data, status=201 if created else 200)


class TeacherDeletionOperationDetailView(APIView):
    permission_classes = [RolePermission]
    allowed_roles = (User.Role.TEACHER, User.Role.ADMIN)

    @extend_schema(responses=ResearchDeletionOperationSerializer)
    def get(self, request, course_id, operation_id):
        course = get_object_or_404(accessible_courses(request.user), pk=course_id)
        require_course_manager(course, request.user)
        operation = get_object_or_404(ResearchDeletionOperation, pk=operation_id, course=course, actor=request.user)
        return Response(ResearchDeletionOperationSerializer(operation_payload(operation)).data)


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
        state = cleanup_state(cleanup.purge_request_id)
        return Response(ResearchDeletionCleanupSerializer({**state,
            'status': 'pending' if state['cleanup_pending'] else 'completed'}).data)
