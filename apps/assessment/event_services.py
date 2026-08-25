from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from .event_schema_registry import validate_event_payload
from .models import (
    AssessmentEvent,
    ClientTimeSync,
    ExperimentSession,
    validate_quality_flags,
)


class EventIdempotencyConflict(RuntimeError):
    def __init__(self, existing_event_id):
        self.existing_event_id = existing_event_id
        super().__init__(
            "Client event sequence already exists with different event content."
        )


class EventLinkageError(ValueError):
    pass


def _deduplicate_quality_flags(quality_flags) -> list[str]:
    result = list(dict.fromkeys(quality_flags))
    validate_quality_flags(result)
    return result


def _validate_linkage(
    *,
    session,
    assignment,
    attempt,
    question_snapshot,
    item_instance,
):
    if assignment is not None and assignment.session_id != session.pk:
        raise EventLinkageError("Assignment does not belong to the event session.")
    if attempt is not None and attempt.session_id != session.pk:
        raise EventLinkageError("Attempt does not belong to the event session.")
    if attempt is not None and assignment is not None:
        if attempt.assignment_id != assignment.pk:
            raise EventLinkageError("Attempt does not belong to the event assignment.")
    if assignment is not None and question_snapshot is not None:
        if assignment.snapshot_id != question_snapshot.pk:
            raise EventLinkageError(
                "Question snapshot does not belong to the event assignment."
            )
    if assignment is not None and item_instance is not None:
        if assignment.item_instance_id != item_instance.pk:
            raise EventLinkageError(
                "Item instance does not belong to the event assignment."
            )


def _event_matches(existing, values) -> bool:
    comparable_fields = (
        "event_domain",
        "event_type",
        "event_version",
        "envelope_version",
        "actor_source",
        "session_id",
        "assignment_id",
        "attempt_id",
        "question_snapshot_id",
        "item_instance_id",
        "client_instance_id",
        "client_sequence_no",
        "correlation_id",
        "client_wall_time_utc",
        "client_monotonic_ms",
        "client_time_origin",
        "time_sync_id",
        "estimated_clock_offset_ms",
        "estimated_rtt_ms",
        "estimated_occurred_at_utc",
        "capture_mode",
        "quality_flags",
        "payload",
        "app_version",
    )
    return all(getattr(existing, field) == values[field] for field in comparable_fields)


def _resolve_idempotent_replay(*, client_instance_id, client_sequence_no, values):
    if client_instance_id is None:
        return None
    existing = AssessmentEvent.objects.filter(
        client_instance_id=client_instance_id,
        client_sequence_no=client_sequence_no,
    ).first()
    if existing is None:
        return None
    if _event_matches(existing, values):
        return existing
    raise EventIdempotencyConflict(existing.event_id)


def append_assessment_event(
    *,
    event_type,
    event_version,
    event_domain,
    actor_source,
    session,
    payload,
    assignment=None,
    attempt=None,
    question_snapshot=None,
    item_instance=None,
    client_instance_id=None,
    client_sequence_no=None,
    correlation_id=None,
    client_wall_time_utc=None,
    client_monotonic_ms=None,
    client_time_origin=None,
    time_sync=None,
    capture_mode="native",
    quality_flags=(),
    app_version="",
) -> AssessmentEvent:
    validate_event_payload(event_type, event_version, payload)
    if (client_instance_id is None) != (client_sequence_no is None):
        raise ValueError(
            "client_instance_id and client_sequence_no must be supplied together."
        )
    _validate_linkage(
        session=session,
        assignment=assignment,
        attempt=attempt,
        question_snapshot=question_snapshot,
        item_instance=item_instance,
    )

    normalized_flags = _deduplicate_quality_flags(quality_flags)
    estimated_clock_offset_ms = None
    estimated_rtt_ms = None
    estimated_occurred_at_utc = None
    sync_is_valid = (
        time_sync is not None
        and time_sync.sync_quality == ClientTimeSync.SyncQuality.VALID
        and client_instance_id is not None
        and time_sync.client_instance_id == client_instance_id
    )
    if sync_is_valid:
        estimated_clock_offset_ms = time_sync.estimated_clock_offset_ms
        estimated_rtt_ms = time_sync.estimated_rtt_ms
        if client_wall_time_utc is not None:
            estimated_occurred_at_utc = client_wall_time_utc + timedelta(
                milliseconds=estimated_clock_offset_ms
            )
    elif client_wall_time_utc is not None and "clock_unsynchronized" not in normalized_flags:
        normalized_flags.append("clock_unsynchronized")

    values = {
        "event_domain": event_domain,
        "event_type": event_type,
        "event_version": event_version,
        "envelope_version": 1,
        "actor_source": actor_source,
        "session_id": session.pk,
        "assignment_id": assignment.pk if assignment is not None else None,
        "attempt_id": attempt.pk if attempt is not None else None,
        "question_snapshot_id": (
            question_snapshot.pk if question_snapshot is not None else None
        ),
        "item_instance_id": item_instance.pk if item_instance is not None else None,
        "client_instance_id": client_instance_id,
        "client_sequence_no": client_sequence_no,
        "correlation_id": correlation_id,
        "client_wall_time_utc": client_wall_time_utc,
        "client_monotonic_ms": client_monotonic_ms,
        "client_time_origin": client_time_origin,
        "time_sync_id": time_sync.pk if time_sync is not None else None,
        "estimated_clock_offset_ms": estimated_clock_offset_ms,
        "estimated_rtt_ms": estimated_rtt_ms,
        "estimated_occurred_at_utc": estimated_occurred_at_utc,
        "capture_mode": capture_mode,
        "quality_flags": normalized_flags,
        "payload": payload,
        "app_version": app_version,
    }

    with transaction.atomic():
        ExperimentSession.objects.select_for_update().get(pk=session.pk)
        replay = _resolve_idempotent_replay(
            client_instance_id=client_instance_id,
            client_sequence_no=client_sequence_no,
            values=values,
        )
        if replay is not None:
            return replay

        next_ingest_sequence = (
            AssessmentEvent.objects.filter(session_id=session.pk).aggregate(
                maximum=Max("server_ingest_sequence_no")
            )["maximum"]
            or 0
        ) + 1
        received_at = timezone.now()
        event = AssessmentEvent(
            **values,
            server_ingest_sequence_no=next_ingest_sequence,
            server_received_at=received_at,
            server_committed_at=timezone.now(),
        )
        event.full_clean(validate_unique=False, validate_constraints=False)
        try:
            with transaction.atomic():
                event.save(force_insert=True)
        except IntegrityError:
            replay = _resolve_idempotent_replay(
                client_instance_id=client_instance_id,
                client_sequence_no=client_sequence_no,
                values=values,
            )
            if replay is not None:
                return replay
            raise
        return event
