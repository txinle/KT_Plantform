from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.api.permissions import RolePermission
from apps.api.exceptions import ApiConflict
from ..research_deletion import DeletionConflict
from ..assignment_return import preview_assignment_return, return_assignment


class AssignmentReturnPreviewSerializer(serializers.Serializer):
    participant_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    class_name = serializers.CharField()
    question_count = serializers.IntegerField()
    submission_count = serializers.IntegerField()
    can_return = serializers.BooleanField()
    blocked_reason = serializers.CharField(allow_blank=True)
    confirmation_token = serializers.CharField(allow_null=True)


class AssignmentReturnInputSerializer(serializers.Serializer):
    confirmation_token = serializers.CharField(max_length=4096)


class AssignmentReturnResultSerializer(serializers.Serializer):
    participant_id = serializers.IntegerField()
    session_id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=['not_started'])


class TeacherAssignmentReturnView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    @extend_schema(operation_id='teacher_assignment_return_preview', responses=AssignmentReturnPreviewSerializer)
    def get(self, request, task_id, participant_id):
        return Response(preview_assignment_return(task_id=task_id, participant_id=participant_id, actor=request.user))

    @extend_schema(operation_id='teacher_assignment_return', request=AssignmentReturnInputSerializer,
        responses=AssignmentReturnResultSerializer)
    def post(self, request, task_id, participant_id):
        serializer = AssignmentReturnInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return Response(return_assignment(task_id=task_id, participant_id=participant_id, actor=request.user,
                confirmation_token=serializer.validated_data['confirmation_token']))
        except DeletionConflict as exc:
            raise ApiConflict('相关记录正在处理，请稍后重新核对并打回。', code='RETURN_BUSY') from exc
