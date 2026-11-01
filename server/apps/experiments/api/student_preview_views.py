from django.core.exceptions import ValidationError
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.exceptions import ApiConflict
from ..student_preview import student_preview_projection
from .views import TeacherExperimentApiMixin


class StudentPreviewUnitSerializer(serializers.Serializer):
    key = serializers.CharField()
    kind = serializers.CharField()
    maximum_score = serializers.CharField()
    question = serializers.JSONField(allow_null=True)
    group = serializers.JSONField(allow_null=True)


class TeacherStudentPreviewSerializer(serializers.Serializer):
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    source = serializers.ChoiceField(choices=["draft", "frozen"])
    units = StudentPreviewUnitSerializer(many=True)


class TeacherStudentPreviewView(TeacherExperimentApiMixin, APIView):
    @extend_schema(operation_id="teacher_experiment_student_preview", responses=TeacherStudentPreviewSerializer)
    def get(self, request, course_id, experiment_id):
        course = self.get_course()
        if not course.can_manage(request.user):
            raise Http404
        experiment = self.get_experiment(course)
        try:
            projection = student_preview_projection(experiment)
        except ValidationError as error:
            raise ApiConflict("无法生成模拟题目：" + "；".join(error.messages), code="STUDENT_PREVIEW_UNAVAILABLE") from error
        return Response(projection)
