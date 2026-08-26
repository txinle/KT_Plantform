import json

from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.http import JsonResponse

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
from .time_sync_services import create_pending_time_sync, finalize_time_sync


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
        acknowledgements = append_event_batch(
            student=request.user,
            document=document,
        )
    except ProcessConflictError as error:
        return _error(error.code, status=error.status)
    except ProcessRequestError as error:
        return _error(error.code, status=error.status)
    return JsonResponse(
        {
            "success": True,
            "acknowledgements": acknowledgements,
        }
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
