import json
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST
from apps.accounts.permissions import role_required
from apps.accounts.models import CourseEnrollment
from apps.api.frontend_entry import frontend_response
from apps.experiments.models import ClassExperimentTask, Experiment
from .models import (
    Attempt,
    ExperimentSession,
    StudentQuestionAssignment,
)
from .confidence_services import frozen_feedback_payload
from .writer_lease_services import WriterLeaseConflict
from .services import (
    SubmissionValidationError,
    finish_session,
    get_or_create_session,
    mark_skipped,
    record_submission_rejection,
    save_draft,
    submit_attempt,
)


def _client_time(value):
    return parse_datetime(value) if value else None


def _json(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


def _next_assignment(session, current_id=None):
    candidates = list(session.assignments.select_related("attempt").order_by("assigned_sequence_index"))
    current = next((item for item in candidates if item.id == current_id), None)
    if current:
        after = [item for item in candidates if item.assigned_sequence_index > current.assigned_sequence_index]
        before = [item for item in candidates if item.assigned_sequence_index <= current.assigned_sequence_index]
        candidates = after + before
    for item in candidates:
        if item.id != current_id and item.attempt.status not in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
            return item
    return None


def _next_url_after_attempt(attempt, *, finish_if_last):
    next_assignment = _next_assignment(attempt.session, attempt.assignment_id)
    if next_assignment:
        return reverse(
            "assessment:question",
            args=[attempt.session_id, next_assignment.id],
        )
    if finish_if_last:
        finish_session(attempt.session_id)
    return reverse("assessment:complete", args=[attempt.session_id])


@role_required("student")
def dashboard(request):
    return frontend_response()


@role_required("student")
def course_dashboard(request, course_id):
    enrollment = get_object_or_404(
        CourseEnrollment.objects.select_related("course", "class_group"),
        course_id=course_id,
        student=request.user,
        is_active=True,
    )
    return frontend_response()


def _student_learning_enrollment(request, course_id):
    if not settings.STUDENT_LEARNING_ANALYTICS_ENABLED:
        raise Http404
    return get_object_or_404(
        CourseEnrollment.objects.select_related("course", "class_group"),
        course_id=course_id,
        student=request.user,
        is_active=True,
    )


@require_GET
@role_required("student")
def student_learning(request, course_id):
    _student_learning_enrollment(request, course_id)
    return frontend_response()


@require_GET
@role_required("student")
def student_learning_experiment(request, course_id, experiment_id):
    _student_learning_enrollment(request, course_id)
    return frontend_response()


@require_GET
@role_required("student")
def student_learning_question(request, course_id, experiment_id, question_id):
    _student_learning_enrollment(request, course_id)
    return frontend_response()


@require_POST
@role_required("student")
def start_experiment(request, experiment_id):
    request_received_at = timezone.now()
    experiment = get_object_or_404(Experiment.objects.select_related("course"), pk=experiment_id)
    tasks = list(
        ClassExperimentTask.objects.filter(
            experiment=experiment,
            participant_snapshots__student=request.user,
            participant_snapshots__version_id=F("release_snapshot_id"),
            participant_snapshots__class_snapshot_id=F("class_snapshot_id"),
        )
        .distinct()
        .order_by("pk")[:2]
    )
    if len(tasks) != 1:
        messages.error(request, "无法确定唯一的班级实验任务，请返回课程重新进入。")
        return redirect("assessment:dashboard")
    try:
        session = get_or_create_session(
            experiment,
            request.user,
            request_received_at=request_received_at,
            class_task=tasks[0],
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("assessment:dashboard")
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        return redirect("assessment:complete", session_id=session.id)
    assignment = session.current_assignment or session.assignments.first()
    return redirect("assessment:question", session_id=session.id, assignment_id=assignment.id)


@role_required("student")
def question_page(request, session_id, assignment_id):
    session = get_object_or_404(
        ExperimentSession.objects.select_related("version__experiment"),
        pk=session_id,
        student=request.user,
    )
    get_object_or_404(
        StudentQuestionAssignment,
        pk=assignment_id,
        session=session,
    )
    response = frontend_response()
    if (
        session.capture_contract_id
        and session.capture_contract.contract_name == "capture-v2"
    ):
        response["X-Capture-Contract"] = "capture-v2"
        if response.has_header("X-Accel-Redirect"):
            response["X-Accel-Redirect"] = "/__frontend/capture-v2.html"
    return response


@role_required("student")
@require_POST
def save_draft_api(request, attempt_id):
    request_received_at = timezone.now()
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    data = _json(request)
    try:
        attempt = save_draft(
            attempt.id,
            data.get("answer"),
            _client_time(data.get("client_occurred_at")),
            request_received_at=request_received_at,
            target_event_id=data.get('target_event_id'),
            expected_version=data.get('expected_version'),
            writer_lease_token=data.get('writer_lease_token'),
            writer_client_instance_id=data.get('writer_client_instance_id'),
        )
        response = {"success": True, "saved_at": attempt.draft_saved_at.isoformat(),
                    "response_version": attempt.response_version,
                    "active_response_event_id": str(attempt.active_response_event_id) if attempt.active_response_event_id else None}
        if attempt.saved_response_snapshot is not None:
            response.update(
                {
                    "response_snapshot_id": str(attempt.saved_response_snapshot.pk),
                    "content_hash": attempt.saved_response_snapshot.content_hash,
                    "content_length": attempt.saved_response_snapshot.content_length,
                    "state_hash": attempt.saved_response_snapshot.state_hash,
                }
            )
        elif attempt.saved_response_state is not None:
            response["response_state_after"] = attempt.saved_response_state
        elif attempt.assignment.snapshot.question_type in {
            "single_choice",
            "multiple_choice",
            "true_false",
        }:
            response["response_state_after"] = None
        return JsonResponse(response)
    except WriterLeaseConflict:
        return JsonResponse({'success': False, 'error': {'code': 'writer_lease_conflict', 'message': '另一设备正在保存此题，请稍后重试；本机轨迹仍保留。'}}, status=409)
    except ValidationError as exc:
        from .capture_v2_services import (
            ResponseVersionConflict,
            record_response_pointer_conflict,
        )
        if isinstance(exc, ResponseVersionConflict):
            record_response_pointer_conflict(
                attempt=attempt,
                conflict=exc,
                requested_expected_version=data.get('expected_version'),
                request_client_instance_id=data.get('writer_client_instance_id'),
            )
            return JsonResponse({'success': False, 'error': {'code': 'RESPONSE_VERSION_CONFLICT', 'message': str(exc), 'fields': exc.document}}, status=409)
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=409)


@role_required("student")
@require_POST
def submit_api(request, attempt_id):
    request_received_at = timezone.now()
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    data = _json(request)
    try:
        attempt = submit_attempt(
            attempt.id,
            data.get("submission_token"),
            _client_time(data.get("client_clicked_at")),
            request_received_at=request_received_at,
            target_event_id=data.get('target_event_id'),
            writer_lease_token=data.get('writer_lease_token'),
            writer_client_instance_id=data.get('writer_client_instance_id'),
            confidence_response=data.get('confidence'),
        )
        response = {
            "success": True,
            "status": attempt.status,
            "next_url": _next_url_after_attempt(attempt, finish_if_last=True),
            "confidence_required": False,
            **frozen_feedback_payload(attempt),
        }
        return JsonResponse(response)
    except WriterLeaseConflict:
        return JsonResponse({'success': False, 'error': {'code': 'writer_lease_conflict', 'message': '另一设备正在保存此题，请稍后重试；本机轨迹仍保留。'}}, status=409)
    except SubmissionValidationError as exc:
        record_submission_rejection(
            attempt_id=attempt.pk,
            submission_token=data.get("submission_token"),
            reason_code=exc.reason_code,
        )
        return JsonResponse(
            {
                "success": False,
                "error": "; ".join(exc.messages),
                "code": exc.reason_code.upper(),
            },
            status=409 if exc.reason_code == "task_terminated" else 400,
        )
    except ValidationError as exc:
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=400)


@role_required("student")
@require_POST
def skip_api(request, attempt_id):
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    try:
        mark_skipped(attempt.id)
        next_assignment = _next_assignment(attempt.session, attempt.assignment_id)
        if next_assignment:
            next_url = reverse("assessment:question", args=[attempt.session_id, next_assignment.id])
        else:
            finish_session(attempt.session_id)
            next_url = reverse("assessment:complete", args=[attempt.session_id])
        return JsonResponse({"success": True, "next_url": next_url})
    except ValidationError as exc:
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=409)


@role_required("student")
@require_POST
def finish(request, session_id):
    session = get_object_or_404(ExperimentSession, pk=session_id, student=request.user)
    finish_session(session.id)
    return redirect("assessment:complete", session_id=session.id)


@role_required("student")
def complete(request, session_id):
    session = get_object_or_404(ExperimentSession.objects.select_related("version__experiment"), pk=session_id, student=request.user)
    return frontend_response()
