"""Read the durable receipt even if processing or its first response was lost."""
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from config.capture_timing import request_phase
from ..event_batch_services import ProcessRequestError
from ..session_submission_models import SessionSubmission
from ..session_submission_services import accept_session_submission, submission_receipt
from .views import StudentApiMixin


class ExpectedVersionSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField(min_value=1)
    response_version = serializers.IntegerField(min_value=0)
    target_event_id = serializers.UUIDField(allow_null=True)


class SessionSubmissionRequestSerializer(serializers.Serializer):
    submission_token = serializers.UUIDField()
    client_clicked_at = serializers.DateTimeField(required=False, allow_null=True)
    expected_versions = ExpectedVersionSerializer(many=True)


class SessionSubmissionPublicErrorSerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=['submission_needs_verification'])
    message = serializers.CharField()


class SubmissionWorkerStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['connected', 'not_connected', 'unknown'])
    observed_at = serializers.DateTimeField()


class SessionSubmissionReceiptSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    submission_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=SessionSubmission.Status.choices)
    processing_state = serializers.ChoiceField(choices=[
        'queued', 'running', 'retrying', 'recovering', 'completed', 'failed'])
    worker = SubmissionWorkerStatusSerializer()
    accepted_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)
    error = SessionSubmissionPublicErrorSerializer(allow_null=True)
    status_url = serializers.CharField()
    next_url = serializers.CharField()


def submit_session_request(request, session_id):
    try:
        job = accept_session_submission(student=request.user, session_id=session_id, document=request.data,
            request_received_at=getattr(request, 'request_received_at', None) or timezone.now())
    except ProcessRequestError as error:
        return Response({'success': False, 'code': error.code}, status=error.status)
    except ValidationError:
        return Response({'success': False, 'code': 'submission_not_authorized'}, status=409)
    return Response(submission_receipt(job), status=202, headers={'Cache-Control': 'no-store'})


class StudentSessionSubmissionView(StudentApiMixin, APIView):
    @extend_schema(request=SessionSubmissionRequestSerializer, responses={202: SessionSubmissionReceiptSerializer})
    def post(self, request, session_id):
        return submit_session_request(request, session_id)

    @extend_schema(responses=SessionSubmissionReceiptSerializer)
    def get(self, request, session_id):
        with request_phase(request, 'submission_status'):
            job = get_object_or_404(SessionSubmission.objects.select_related('session'),
                                    session_id=session_id, session__student=request.user)
        return Response(submission_receipt(job), headers={'Cache-Control': 'no-store'})
