import json
from django.core.exceptions import ValidationError
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
from .writer_lease_services import WriterLeaseConflict
from .atomic_submission_services import MAX_BYTES
from .event_batch_services import ProcessRequestError
from .services import save_draft


def _client_time(value):
    return parse_datetime(value) if value else None


def _json(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


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
            from .transition_entry_services import append_next_entry
            receipt = append_next_entry(request, attempt_id, data, receipt)
            return JsonResponse(receipt)
        if 'capture_bundle' in data:
            from .capture_bundle_services import save_draft_capture_bundle
            if len(request.body) > MAX_BYTES:
                raise ProcessRequestError('request_too_large', status=413)
            attempt, receipt = save_draft_capture_bundle(student=request.user, attempt_id=attempt_id,
                document=data, request_received_at=request_received_at)
            return JsonResponse(receipt)
        else:
            attempt = save_draft(
                attempt.id,
                data.get("answer"),
                _client_time(data.get("client_occurred_at")),
                request_received_at=request_received_at,
                target_event_id=data.get('target_event_id'),
                expected_version=data.get('expected_version'),
                writer_lease_token=data.get('writer_lease_token'),
                writer_client_instance_id=data.get('writer_client_instance_id'),
                inline_confidence=data.get('inline_confidence'),
            )
        from .capture_bundle_services import draft_response
        return JsonResponse(draft_response(attempt))
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


def _whole_paper_required():
    return JsonResponse({'success': False, 'error': {
        'code': 'WHOLE_SESSION_SUBMISSION_REQUIRED',
        'message': '请保存草稿，通过提交试卷完成作答。',
    }}, status=409)


@role_required("student")
@require_POST
def submit_api(request, attempt_id):
    get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    return _whole_paper_required()


@role_required("student")
@require_POST
def skip_api(request, attempt_id):
    get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    return _whole_paper_required()


@role_required("student")
@require_POST
def finish(request, session_id):
    session = get_object_or_404(ExperimentSession, pk=session_id, student=request.user)
    if session.assessment_flow_version != 1:
        return _whole_paper_required()
    from .session_submission_services import accept_session_submission, submission_receipt
    try:
        job = accept_session_submission(student=request.user, session_id=session_id,
            document=json.loads(request.body or b'{}'), request_received_at=timezone.now())
        return JsonResponse(submission_receipt(job), status=202)
    except (ValidationError, ValueError) as error:
        return JsonResponse({'success': False, 'code': getattr(error, 'code', 'SESSION_SUBMISSION_INVALID'),
                             'message': str(error)}, status=getattr(error, 'status', 409))
