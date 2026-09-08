"""Read-only recovery of the original task and final submission."""
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import CourseEnrollment
from ..deadline_services import DeadlineDecisionService
from ..feedback_services import frozen_feedback_payload
from ..models import Attempt, ExperimentSession, SubmissionEvent
from ..views import _next_url_after_attempt
from .views import StudentApiMixin, _student_task, _entry_projection


class RecoverySerializer(serializers.Serializer):
    task_id = serializers.IntegerField(required=False)
    session_id = serializers.IntegerField(required=False)
    status = serializers.CharField()
    code = serializers.CharField()
    can_continue = serializers.BooleanField()
    question_url = serializers.CharField(allow_null=True)
    submission_matches = serializers.BooleanField(required=False)
    result = serializers.JSONField(allow_null=True, required=False)


def recovery_permission(session, attempt=None):
    course_id = session.version.experiment.course_id
    if course_id and not CourseEnrollment.objects.filter(course_id=course_id, student=session.student, is_active=True).exists():
        raise Http404
    if session.class_task_id:
        _student_task(session.student, session.class_task_id)
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        return False, 'SESSION_CLOSED'
    if course_id and session.version.experiment.course.status != 'active':
        return False, 'COURSE_CLOSED'
    if session.class_task_id:
        decision = DeadlineDecisionService.can_write_response(session.class_task, session, attempt, timezone.now())
        return decision.allowed, decision.code
    deadline = (attempt.initial_effective_deadline_at if attempt else None) or session.deadline_at
    if deadline and timezone.now() >= deadline:
        return False, 'DEADLINE_EXPIRED'
    return True, ''


class StudentAttemptRecoveryView(StudentApiMixin, APIView):
    @extend_schema(responses=RecoverySerializer)
    def get(self, request, attempt_id=None, session_id=None, assignment_id=None):
        lookup = {'pk': attempt_id} if attempt_id is not None else {'session_id': session_id, 'assignment_id': assignment_id}
        attempt = get_object_or_404(Attempt.objects.select_related(
            'session__version__experiment__course', 'session__student', 'session__class_task',
            'assignment__snapshot',
        ), session__student=request.user, **lookup)
        allowed, code = recovery_permission(attempt.session, attempt)
        submission = SubmissionEvent.objects.filter(attempt=attempt).first()
        result = None
        if submission is not None and attempt.status == Attempt.Status.SUBMITTED:
            result = {
                'success': True, 'status': attempt.status, 'confidence_required': False,
                'next_url': _next_url_after_attempt(attempt, finish_if_last=False),
                **frozen_feedback_payload(attempt),
            }
        return Response({
            'status': attempt.status, 'code': code,
            'can_continue': allowed and attempt.status not in {'submitted', 'timed_out', 'unanswered'},
            'question_url': reverse('assessment:question', args=[attempt.session_id, attempt.assignment_id]),
            'submission_matches': submission is not None and str(submission.submission_token) == request.query_params.get('submission_token'),
            'result': result,
        }, headers={'Cache-Control': 'no-store'})


class StudentTaskRecoveryView(StudentApiMixin, APIView):
    @extend_schema(responses=RecoverySerializer)
    def get(self, request, task_id):
        task = _student_task(request.user, task_id)
        session = ExperimentSession.objects.select_related('version__experiment__course', 'student', 'class_task').filter(
            class_task=task, participant_snapshot__student=request.user,
        ).first()
        if session is None:
            return Response({'status': 'not_started', 'code': 'SESSION_NOT_STARTED', 'can_continue': False, 'question_url': None})
        allowed, code = recovery_permission(session)
        return Response({'status': session.status, 'code': code, 'can_continue': allowed,
                         **_entry_projection(task, session)}, headers={'Cache-Control': 'no-store'})
