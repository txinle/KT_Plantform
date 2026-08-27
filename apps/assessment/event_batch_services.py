from dataclasses import dataclass
from datetime import timezone as datetime_timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .event_invariants import TELEMETRY_EVENTS, SESSION_ONLY_EVENTS
from .event_schema_registry import (
    EventPayloadValidationError,
    EventSchemaNotFound,
    validate_event_payload,
)
from .event_services import EventIdempotencyConflict, append_assessment_event
from .models import Attempt, ClientTimeSync


MAX_BATCH_EVENTS = 100
MAX_CLIENT_MONOTONIC_MS = Decimal("99999999999999999.999")
MONOTONIC_QUANTUM_MS = Decimal("0.001")

CLIENT_BATCH_EVENT_TYPES = frozenset(
    {
        "item_rendered",
        "item_left",
        "response_input_started",
        "response_state_changed",
        "response_commit_requested",
        "feedback_shown",
        "explanation_shown",
        "hint_opened",
        "hint_closed",
        "resource_opened",
        "resource_closed",
        "document_visible",
        "document_hidden",
        "window_focused",
        "window_blurred",
        "pageshow",
        "pagehide",
        "network_online",
        "network_offline",
        "activity_heartbeat",
        "simulation_parameter_changed",
        "simulation_run_started",
        "simulation_run_completed",
        "simulation_error_returned",
    }
) | SESSION_ONLY_EVENTS

BATCH_FIELDS = frozenset({"client_instance_id", "events"})
EVENT_FIELDS = frozenset(
    {
        "event_type",
        "event_id",
        "event_version",
        "session_id",
        "attempt_id",
        "client_sequence_no",
        "correlation_id",
        "client_wall_time_utc",
        "client_monotonic_ms",
        "client_time_origin",
        "time_sync_id",
        "payload",
        "quality_flags",
        "app_version",
    }
)
EVENT_REQUIRED_FIELDS = EVENT_FIELDS - frozenset({"correlation_id", "time_sync_id", "event_id", "attempt_id"})


class ProcessRequestError(ValueError):
    def __init__(self, code, *, status=400):
        self.code = code
        self.status = status
        super().__init__(code)


class ProcessConflictError(ProcessRequestError):
    def __init__(self, code="event_conflict"):
        super().__init__(code, status=409)


def require_exact_mapping(value, *, allowed, required=None, code="invalid_request"):
    if not isinstance(value, dict):
        raise ProcessRequestError(code)
    keys = frozenset(value)
    required = allowed if required is None else frozenset(required)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise ProcessRequestError(code)
    return value


def parse_uuid(value, *, code="invalid_request"):
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ProcessRequestError(code) from error


def parse_positive_integer(value, *, code="invalid_request"):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProcessRequestError(code)
    return value


def parse_finite_decimal(value, *, code="invalid_request"):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ProcessRequestError(code) from error
    if (
        not result.is_finite()
        or result < 0
        or result > MAX_CLIENT_MONOTONIC_MS
    ):
        raise ProcessRequestError(code)
    return result.quantize(MONOTONIC_QUANTUM_MS, rounding=ROUND_HALF_UP)


def parse_aware_datetime(value, *, code="invalid_request"):
    if not isinstance(value, str):
        raise ProcessRequestError(code)
    result = parse_datetime(value)
    if result is None or not timezone.is_aware(result):
        raise ProcessRequestError(code)
    return result.astimezone(datetime_timezone.utc)


@dataclass(frozen=True)
class ParsedClientEvent:
    event_type: str
    event_version: int
    session: object
    assignment: object
    attempt: object
    client_sequence_no: int
    correlation_id: object
    client_wall_time_utc: object
    client_monotonic_ms: Decimal
    client_time_origin: object
    time_sync: object
    payload: dict
    quality_flags: tuple
    app_version: str
    event_id: object = None


def _parse_event(*, student, client_instance_id, document, prior_sequence):
    require_exact_mapping(
        document,
        allowed=EVENT_FIELDS,
        required=EVENT_REQUIRED_FIELDS,
        code="invalid_event",
    )
    event_type = document["event_type"]
    if not isinstance(event_type, str) or event_type not in CLIENT_BATCH_EVENT_TYPES:
        raise ProcessRequestError("invalid_event")
    event_version = parse_positive_integer(
        document["event_version"],
        code="invalid_event",
    )
    payload = document["payload"]
    if not isinstance(payload, dict):
        raise ProcessRequestError("invalid_event")
    try:
        validate_event_payload(event_type, event_version, payload)
    except (EventPayloadValidationError, EventSchemaNotFound) as error:
        raise ProcessRequestError("invalid_event") from error

    sequence = parse_positive_integer(
        document["client_sequence_no"],
        code="invalid_event",
    )
    if prior_sequence is not None and sequence <= prior_sequence:
        raise ProcessRequestError("invalid_event_order")
    session_id = parse_positive_integer(document["session_id"], code="invalid_event")
    attempt_id = None
    if document.get("attempt_id") is not None:
        attempt_id = parse_positive_integer(document["attempt_id"], code="invalid_event")
    if event_type in SESSION_ONLY_EVENTS and attempt_id is None:
        attempt = None
    else:
        if attempt_id is None:
            raise ProcessRequestError("invalid_event")
        attempt = (
        Attempt.objects.select_related(
            "session",
            "assignment__snapshot",
            "assignment__item_instance",
        )
        .filter(
            pk=attempt_id,
            session_id=session_id,
            session__student=student,
        )
        .first()
    )
        if attempt is None:
            raise ProcessRequestError("invalid_event_link")
    session = attempt.session if attempt is not None else __import__("apps.assessment.models", fromlist=["ExperimentSession"]).ExperimentSession.objects.filter(pk=session_id, student=student).first()
    if session is None:
        raise ProcessRequestError("invalid_event_link")

    correlation_id = None
    if document.get("correlation_id") is not None:
        correlation_id = parse_uuid(
            document["correlation_id"],
            code="invalid_event",
        )
    time_sync = None
    if document.get("time_sync_id") is not None:
        time_sync_id = parse_uuid(document["time_sync_id"], code="invalid_event")
        time_sync = ClientTimeSync.objects.filter(
            pk=time_sync_id,
            client_instance_id=client_instance_id,
            session_id=session_id,
            session__student=student,
        ).first()
        if time_sync is None:
            raise ProcessRequestError("invalid_event_link")

    quality_flags = document["quality_flags"]
    if not isinstance(quality_flags, list) or any(
        not isinstance(flag, str) for flag in quality_flags
    ):
        raise ProcessRequestError("invalid_event")
    app_version = document["app_version"]
    if not isinstance(app_version, str) or len(app_version) > 64:
        raise ProcessRequestError("invalid_event")

    return ParsedClientEvent(
        event_type=event_type,
        event_version=event_version,
        session=session,
        assignment=attempt.assignment if attempt is not None else None,
        attempt=attempt,
        client_sequence_no=sequence,
        correlation_id=correlation_id,
        client_wall_time_utc=parse_aware_datetime(
            document["client_wall_time_utc"],
            code="invalid_event",
        ),
        client_monotonic_ms=parse_finite_decimal(
            document["client_monotonic_ms"],
            code="invalid_event",
        ),
        client_time_origin=parse_aware_datetime(
            document["client_time_origin"],
            code="invalid_event",
        ),
        time_sync=time_sync,
        payload=payload,
        quality_flags=tuple(quality_flags),
        app_version=app_version,
        event_id=parse_uuid(document["event_id"], code="invalid_event") if document.get("event_id") is not None else None,
    )


def parse_event_batch(*, student, document):
    require_exact_mapping(document, allowed=BATCH_FIELDS)
    client_instance_id = parse_uuid(document["client_instance_id"])
    event_documents = document["events"]
    if (
        not isinstance(event_documents, list)
        or not event_documents
        or len(event_documents) > MAX_BATCH_EVENTS
    ):
        raise ProcessRequestError("invalid_batch_size")
    parsed = []
    prior_sequence = None
    for event_document in event_documents:
        event = _parse_event(
            student=student,
            client_instance_id=client_instance_id,
            document=event_document,
            prior_sequence=prior_sequence,
        )
        parsed.append(event)
        prior_sequence = event.client_sequence_no
    return client_instance_id, tuple(parsed)


@transaction.atomic
def append_event_batch(*, student, document):
    client_instance_id, events = parse_event_batch(
        student=student,
        document=document,
    )
    acknowledgements = []
    try:
        for parsed in events:
            event_domain = (
                "telemetry"
                if parsed.event_type in TELEMETRY_EVENTS
                else "assessment"
            )
            event = append_assessment_event(
                event_type=parsed.event_type,
                event_version=parsed.event_version,
                event_domain=event_domain,
                actor_source="client",
                session=parsed.session,
                assignment=parsed.assignment,
                attempt=parsed.attempt,
                question_snapshot=parsed.assignment.snapshot if parsed.assignment is not None else None,
                item_instance=parsed.assignment.item_instance if parsed.assignment is not None else None,
                client_instance_id=client_instance_id,
                client_sequence_no=parsed.client_sequence_no,
                correlation_id=parsed.correlation_id,
                client_wall_time_utc=parsed.client_wall_time_utc,
                client_monotonic_ms=parsed.client_monotonic_ms,
                client_time_origin=parsed.client_time_origin,
                time_sync=parsed.time_sync,
                capture_mode="native",
                quality_flags=parsed.quality_flags,
                payload=parsed.payload,
                app_version=parsed.app_version,
                event_id=parsed.event_id,
                envelope_version=2,
            )
            acknowledgements.append(
                {
                    "client_sequence_no": parsed.client_sequence_no,
                    "event_id": str(event.event_id),
                    "server_ingest_sequence_no": event.server_ingest_sequence_no,
                }
            )
    except EventIdempotencyConflict as error:
        raise ProcessConflictError() from error
    except (ValueError, ValidationError) as error:
        raise ProcessRequestError("invalid_event") from error
    return acknowledgements
