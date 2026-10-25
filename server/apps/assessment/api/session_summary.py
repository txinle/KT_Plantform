from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import RolePermission
from apps.assessment.models import ExperimentSession
from .session_submission import ExpectedVersionSerializer


class StudentSessionSummarySerializer(serializers.Serializer):
    session_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    submission_count = serializers.IntegerField()
    attempt_count = serializers.IntegerField()
    return_url = serializers.CharField()
    expected_versions = ExpectedVersionSerializer(many=True)
    submission_status_url = serializers.CharField()


class StudentSessionSummaryView(APIView):
    permission_classes = [RolePermission]
    allowed_roles = ('student',)

    @extend_schema(responses=StudentSessionSummarySerializer)
    def get(self, request, session_id):
        session = get_object_or_404(
            ExperimentSession.objects.select_related('version__experiment'),
            pk=session_id, student=request.user,
        )
        return Response(StudentSessionSummarySerializer({
            'session_id': session.pk,
            'experiment_name': session.version.experiment.name,
            'status': session.status,
            'status_label': session.get_status_display(),
            'submission_count': session.submission_count,
            'attempt_count': session.attempts.count(),
            'return_url': reverse('assessment:dashboard'),
            'expected_versions': [
                {'attempt_id': pk, 'response_version': version, 'target_event_id': head}
                for pk, version, head in session.attempts.order_by('pk').values_list(
                    'pk', 'response_version', 'active_response_event_id')
            ],
            'submission_status_url': reverse('assessment_api:student-session-submission', args=[session.pk]),
        }).data, headers={'Cache-Control': 'no-store'})
