import json

from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.urls import reverse

from apps.accounts.permissions import role_required

from .event_batch_services import (
    ProcessConflictError,
    ProcessRequestError,
    append_event_batch,
    parse_aware_datetime,
    parse_finite_decimal,
    parse_positive_integer,
    parse_uuid,
    require_exact_mapping,
)
from .models import ClientTimeSync, ExperimentSession
from .models import AssessmentEvent
from .recording_capture_services import record_client_capture_checkpoint
from .confidence_services import (
    ConfidenceFlowError,
    commit_confidence,
    frozen_feedback_payload,
    mark_confidence_prompt_shown,
    skip_confidence,
)
from .services import finish_session
from .time_sync_services import create_pending_time_sync, finalize_time_sync
from .writer_lease_services import (
    WriterLeaseConflict,
    acquire_attempt_writer_lease,
    renew_attempt_writer_lease,
    release_attempt_writer_lease,
)


MAX_PROCESS_REQUEST_BYTES = 256 * 1024


def _error(code, *, status):
    return JsonResponse(
        {
            "success": False,
            "error": {
                "code": code,
                "message": "Process data request was rejected.",
            },
        },
        status=status,
    )


def _json_document(request):
    content_type = request.content_type or ""
    if content_type.lower() != "application/json":
        raise ProcessRequestError("json_required", status=415)
    try:
        declared_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        declared_length = 0
    if declared_length > MAX_PROCESS_REQUEST_BYTES:
        raise ProcessRequestError("request_too_large", status=413)
    if len(request.body) > MAX_PROCESS_REQUEST_BYTES:
        raise ProcessRequestError("request_too_large", status=413)
    try:
        document = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProcessRequestError("invalid_json") from error
    if not isinstance(document, dict):
        raise ProcessRequestError("invalid_request")
    return document


def _sync_document(sample):
    return {
        "success": True,
        "time_sync_id": str(sample.pk),
        "server_received_at": sample.server_received_at.isoformat(),
        "server_responded_at": sample.server_responded_at.isoformat(),
        "sync_quality": sample.sync_quality,
        "estimated_clock_offset_ms": sample.estimated_clock_offset_ms,
        "estimated_rtt_ms": sample.estimated_rtt_ms,
    }


@role_required("student")
@require_POST
def event_batch(request):
    try:
        document = _json_document(request)
        require_exact_mapping(
            document,
            allowed=frozenset({"client_instance_id", "events"}),
        )
        event_documents = document["events"]
        if not isinstance(event_documents, list) or not 1 <= len(event_documents) <= 100:
            raise ProcessRequestError("invalid_batch_size")
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    acknowledgements = []
    rejections = []
    first_error = None
    ordered_documents = sorted(
        event_documents,
        key=lambda item: (
            item.get("client_sequence_no", 0)
            if isinstance(item, dict)
            and isinstance(item.get("client_sequence_no"), int)
            else 0
        ),
    )
    for event_document in ordered_documents:
        sequence_no = (
            event_document.get("client_sequence_no")
            if isinstance(event_document, dict)
            else None
        )
        try:
            acknowledgements.extend(
                append_event_batch(
                    student=request.user,
                    document={
                        "client_instance_id": document["client_instance_id"],
                        "events": [event_document],
                    },
                )
            )
        except (ProcessConflictError, ProcessRequestError) as error:
            first_error = first_error or error
            rejections.append(
                {
                    "client_sequence_no": sequence_no,
                    "code": error.code,
                    "permanent": error.status < 500,
                }
            )
    if not acknowledgements and first_error is not None:
        return _error(first_error.code, status=first_error.status)
    client_instance_id = document.get("client_instance_id")
    received = list(
        AssessmentEvent.objects.filter(
            client_instance_id=client_instance_id,
            student=request.user,
        )
        .order_by("client_sequence_no")
        .values_list("client_sequence_no", flat=True)
    )
    received_set = set(received)
    highest = max(received, default=0)
    contiguous = 0
    while contiguous + 1 in received_set:
        contiguous += 1
    missing = []
    start = None
    for sequence in range(1, highest + 1):
        if sequence not in received_set and start is None:
            start = sequence
        if sequence in received_set and start is not None:
            missing.append([start, sequence - 1])
            start = None
    if start is not None:
        missing.append([start, highest])
    return JsonResponse(
        {
            "success": True,
            "acknowledgements": acknowledgements,
            "rejections": rejections,
            "highest_received_sequence_no": highest,
            "highest_contiguous_received_sequence_no": contiguous,
            "missing_sequence_ranges": missing,
        }
    )


@role_required("student")
@require_POST
def capture_checkpoint(request):
    try:
        checkpoint = record_client_capture_checkpoint(
            student=request.user,
            document=_json_document(request),
        )
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except (KeyError, ValueError, ValidationError):
        return _error("invalid_checkpoint", status=400)
    return JsonResponse(
        {
            "success": True,
            "checkpoint_id": str(checkpoint.pk),
            "server_received_at": checkpoint.server_received_at.isoformat(),
        },
        status=201,
    )


@role_required("student")
@require_POST
def time_sync_create(request):
    try:
        document = _json_document(request)
        require_exact_mapping(
            document,
            allowed=frozenset(
                {
                    "session_id",
                    "client_instance_id",
                    "client_wall_time_utc",
                    "client_monotonic_ms",
                    "client_time_origin",
                }
            ),
        )
        session_id = parse_positive_integer(document["session_id"])
        session = ExperimentSession.objects.filter(
            pk=session_id,
            student=request.user,
        ).first()
        if session is None:
            raise ProcessRequestError("invalid_time_sync_link")
        sample = create_pending_time_sync(
            session=session,
            client_instance_id=parse_uuid(document["client_instance_id"]),
            client_wall_time_utc=parse_aware_datetime(
                document["client_wall_time_utc"]
            ),
            client_monotonic_ms=parse_finite_decimal(
                document["client_monotonic_ms"]
            ),
            client_time_origin=parse_aware_datetime(document["client_time_origin"]),
        )
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except (ValueError, ValidationError):
        return _error("invalid_time_sync", status=400)
    response = JsonResponse(_sync_document(sample), status=201)
    return response


@role_required("student")
@require_POST
def time_sync_finalize(request):
    try:
        document = _json_document(request)
        require_exact_mapping(
            document,
            allowed=frozenset(
                {
                    "time_sync_id",
                    "client_instance_id",
                    "client_received_wall_time_utc",
                    "client_received_monotonic_ms",
                }
            ),
        )
        time_sync_id = parse_uuid(document["time_sync_id"])
        if not ClientTimeSync.objects.filter(
            pk=time_sync_id,
            session__student=request.user,
        ).exists():
            raise ProcessRequestError("invalid_time_sync_link")
        sample = finalize_time_sync(
            time_sync_id=time_sync_id,
            client_instance_id=parse_uuid(document["client_instance_id"]),
            client_received_wall_time_utc=parse_aware_datetime(
                document["client_received_wall_time_utc"]
            ),
            client_received_monotonic_ms=parse_finite_decimal(
                document["client_received_monotonic_ms"]
            ),
        )
    except ProcessConflictError as error:
        return _error(error.code, status=error.status)
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except (ValueError, ValidationError):
        return _error("time_sync_conflict", status=409)
    return JsonResponse(_sync_document(sample))


def _owned_attempt(attempt_id, student):
    from .models import Attempt

    attempt = Attempt.objects.filter(pk=attempt_id, session__student=student).first()
    if attempt is None:
        raise ProcessRequestError("invalid_writer_lease_link")
    return attempt


@role_required("student")
@require_POST
def writer_lease_acquire(request, attempt_id):
    try:
        document = _json_document(request)
        require_exact_mapping(document, allowed=frozenset({"client_instance_id"}))
        attempt = _owned_attempt(attempt_id, request.user)
        lease = acquire_attempt_writer_lease(
            attempt,
            client_instance_id=parse_uuid(document["client_instance_id"]),
        )
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except (ValueError, WriterLeaseConflict):
        return _error("writer_lease_conflict", status=409)
    return JsonResponse(
        {
            "success": True,
            "lease_token": str(lease.token),
            "client_instance_id": str(lease.client_instance_id),
            "expires_at": lease.expires_at.isoformat(),
        },
        status=201,
    )


@role_required("student")
@require_POST
def writer_lease_renew(request, attempt_id):
    try:
        document = _json_document(request)
        require_exact_mapping(
            document,
            allowed=frozenset({"lease_token", "client_instance_id"}),
        )
        attempt = _owned_attempt(attempt_id, request.user)
        lease = renew_attempt_writer_lease(
            attempt,
            lease_token=parse_uuid(document["lease_token"]),
            client_instance_id=parse_uuid(document["client_instance_id"]),
        )
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except (ValueError, WriterLeaseConflict):
        return _error("writer_lease_conflict", status=409)
    return JsonResponse(
        {
            "success": True,
            "lease_token": str(lease.token),
            "client_instance_id": str(lease.client_instance_id),
            "expires_at": lease.expires_at.isoformat(),
        }
    )


@role_required("student")
@require_POST
def writer_lease_release(request, attempt_id):
    try:
        document = _json_document(request)
        require_exact_mapping(
            document,
            allowed=frozenset({"lease_token", "client_instance_id"}),
        )
        attempt = _owned_attempt(attempt_id, request.user)
        release_attempt_writer_lease(
            attempt,
            lease_token=parse_uuid(document["lease_token"]),
            client_instance_id=parse_uuid(document["client_instance_id"]),
        )
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except (ValueError, WriterLeaseConflict):
        return _error("writer_lease_conflict", status=409)
    return JsonResponse({"success": True})


def _resolved_confidence_document(assignment):
    attempt = assignment.attempt
    from .views import _next_assignment

    next_assignment = _next_assignment(attempt.session, attempt.assignment_id)
    if next_assignment is None:
        finish_session(attempt.session_id)
        next_url = reverse("assessment:complete", args=[attempt.session_id])
    else:
        next_url = reverse(
            "assessment:question",
            args=[attempt.session_id, next_assignment.pk],
        )
    return {
        "success": True,
        "confidence_required": False,
        "next_url": next_url,
        **frozen_feedback_payload(attempt),
    }


@role_required("student")
@require_POST
def confidence_prompt_shown(request, attempt_id):
    try:
        document = _json_document(request)
        require_exact_mapping(document, allowed=frozenset())
        assignment = mark_confidence_prompt_shown(
            attempt_id=attempt_id,
            student=request.user,
        )
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except ConfidenceFlowError:
        return _error("confidence_conflict", status=409)
    return JsonResponse(
        {
            "success": True,
            "response_status": assignment.response_status,
        }
    )


@role_required("student")
@require_POST
def confidence_commit(request, attempt_id):
    try:
        document = _json_document(request)
        require_exact_mapping(
            document,
            allowed=frozenset({"probability"}),
        )
        assignment = commit_confidence(
            attempt_id=attempt_id,
            student=request.user,
            probability=document["probability"],
        )
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except ConfidenceFlowError:
        return _error("confidence_conflict", status=409)
    return JsonResponse(_resolved_confidence_document(assignment))


@role_required("student")
@require_POST
def confidence_skip(request, attempt_id):
    try:
        document = _json_document(request)
        require_exact_mapping(document, allowed=frozenset())
        assignment = skip_confidence(
            attempt_id=attempt_id,
            student=request.user,
        )
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    except ConfidenceFlowError:
        return _error("confidence_conflict", status=409)
    return JsonResponse(_resolved_confidence_document(assignment))
