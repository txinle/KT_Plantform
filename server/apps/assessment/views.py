import json
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.http import JsonResponse
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from apps.accounts.permissions import role_required
from .models import (
    Attempt,
    ExperimentSession,
)
from .confidence_services import frozen_feedback_payload
from .writer_lease_services import WriterLeaseConflict
from .atomic_submission_services import MAX_BYTES, submit_attempt_bundle
from .event_batch_services import ProcessRequestError
from .services import (
    SubmissionValidationError,
    finish_session,
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
@require_POST
@transaction.non_atomic_requests
def save_draft_api(request, attempt_id):
    request_received_at = timezone.now()
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    data = _json(request)
    try:
        if 'save_and_leave' in data:
            from .saved_navigation_services import save_and_leave_attempt
            if len(request.body) > MAX_BYTES:
                raise ProcessRequestError('save_and_leave_too_large', status=413)
            _, receipt = save_and_leave_attempt(student=request.user, attempt_id=attempt_id,
                                               document=data, request_received_at=request_received_at)
            return JsonResponse(receipt)
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
    except ProcessRequestError as error:
        fields = _navigation_rejection_fields(error)
        return JsonResponse({'success': False, 'error': {'code': error.code, 'fields': fields}}, status=error.status)
    except WriterLeaseConflict as error:
        return JsonResponse({'success': False, 'error': {'code': 'writer_lease_conflict', 'message': '另一设备正在保存此题，请稍后重试；本机轨迹仍保留。', 'fields': _navigation_rejection_fields(error)}}, status=409)
    except ValidationError as exc:
        from .capture_v2_services import (
            ResponseVersionConflict,
            record_response_pointer_conflict,
        )
        if isinstance(exc, ResponseVersionConflict):
            record_response_pointer_conflict(
                attempt=attempt,
                conflict=exc,
                requested_expected_version=data.get('save_and_leave', {}).get('expected_version') if 'save_and_leave' in data else data.get('expected_version'),
                request_client_instance_id=data.get('save_and_leave', {}).get('client_instance_id') if 'save_and_leave' in data else data.get('writer_client_instance_id'),
            )
            return JsonResponse({'success': False, 'error': {'code': 'RESPONSE_VERSION_CONFLICT', 'message': str(exc), 'fields': {**exc.document, **_navigation_rejection_fields(exc)}}}, status=409)
        if 'save_and_leave' in data:
            return JsonResponse({'success': False, 'error': {'code': 'navigation_rejected', 'message': '; '.join(exc.messages), 'fields': _navigation_rejection_fields(exc)}}, status=409)
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=409)


def _navigation_rejection_fields(error):
    token = getattr(error, 'navigation_rejection_token', None)
    return {'navigation_rejected': True, 'navigation_token': token} if token else {}


@role_required("student")
@require_POST
@transaction.non_atomic_requests
def submit_api(request, attempt_id):
    request_received_at = timezone.now()
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    try:
        body_size = len(request.body)
    except RequestDataTooBig:
        body_size = MAX_BYTES + 1
    if body_size > MAX_BYTES:
        return JsonResponse({'success': False, 'error': {'code': 'atomic_submission_too_large'}}, status=413)
    data = _json(request)
    if not isinstance(data, dict):
        return JsonResponse({'success': False, 'error': {'code': 'invalid_request'}}, status=400)
    try:
        receipt = {}
        if 'atomic_submission' in data:
            attempt, receipt = submit_attempt_bundle(
                student=request.user, attempt_id=attempt.id, document=data,
                request_received_at=request_received_at,
            )
        else:
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
            "next_url": _next_url_after_attempt(attempt, finish_if_last='atomic_submission' not in data),
            "confidence_required": False,
            **receipt,
            **frozen_feedback_payload(attempt),
        }
        return JsonResponse(response)
    except ProcessRequestError as exc:
        record_submission_rejection(
            attempt_id=attempt.pk, submission_token=data.get('submission_token'),
            reason_code='conflict' if exc.status == 409 else 'invalid_answer',
            preserve_first_rejection=True,
        )
        return JsonResponse({'success': False, 'error': {'code': exc.code}}, status=exc.status)
    except WriterLeaseConflict:
        if 'atomic_submission' in data:
            record_submission_rejection(
                attempt_id=attempt.pk, submission_token=data.get('submission_token'), reason_code='conflict',
                preserve_first_rejection=True,
            )
        return JsonResponse({'success': False, 'error': {'code': 'writer_lease_conflict', 'message': '另一设备正在保存此题，请稍后重试；本机轨迹仍保留。'}}, status=409)
    except SubmissionValidationError as exc:
        record_submission_rejection(
            attempt_id=attempt.pk,
            submission_token=data.get("submission_token"),
            reason_code=exc.reason_code,
            preserve_first_rejection='atomic_submission' in data,
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
        if 'atomic_submission' in data:
            record_submission_rejection(
                attempt_id=attempt.pk, submission_token=data.get('submission_token'), reason_code='invalid_answer',
                preserve_first_rejection=True,
            )
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
    return JsonResponse({'success': True, 'next_url': reverse('assessment:complete', args=[session.id])})
