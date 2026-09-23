from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.utils import timezone

from .event_invariants import validate_event_invariants, validate_response_state_chain
from .event_schema_registry import (
    event_schema_sha256,
    get_event_schema,
    validate_event_payload,
)
from .models import (
    AssessmentEvent,
    ClientTimeSync,
    EventSchemaArtifact,
    ExperimentSession,
    _assessment_event_append_scope,
    _event_schema_artifact_append_scope,
    validate_quality_flags,
)


TIME_SYNC_VALIDITY_WINDOW_MS = 20 * 60 * 1000


class EventIdempotencyConflict(RuntimeError):
    def __init__(self, existing_event_id):
        self.existing_event_id = existing_event_id
        super().__init__(
            "Client event sequence already exists with different event content."
        )


class EventLinkageError(ValueError):
    pass


class EventSchemaDriftError(RuntimeError):
    pass


def _register_schema_artifact(event_type, event_version):
    current_hash = event_schema_sha256(event_type, event_version)
    current_document = get_event_schema(event_type, event_version)
    try:
        with _event_schema_artifact_append_scope():
            artifact, created = EventSchemaArtifact.objects.get_or_create(
                event_type=event_type,
                event_version=event_version,
                defaults={
                    "schema_sha256": current_hash,
                    "schema_document": current_document,
                },
            )
    except IntegrityError:
        artifact = EventSchemaArtifact.objects.get(
            event_type=event_type,
            event_version=event_version,
        )
        created = False
    if not created and (
        artifact.schema_sha256 != current_hash
        or artifact.schema_document != current_document
    ):
        raise EventSchemaDriftError(
            f"Event schema drift detected for {event_type}:v{event_version}."
        )
    return artifact


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
    effective_assignment = assignment
    if effective_assignment is None and attempt is not None:
        effective_assignment = attempt.assignment
    if effective_assignment is not None and question_snapshot is not None:
        if effective_assignment.snapshot_id != question_snapshot.pk:
            raise EventLinkageError(
                "Question snapshot does not belong to the event assignment."
            )
    if effective_assignment is not None and item_instance is not None:
        if effective_assignment.item_instance_id != item_instance.pk:
            raise EventLinkageError(
                "Item instance does not belong to the event assignment."
            )
    if question_snapshot is not None and item_instance is not None:
        if item_instance.question_snapshot_id != question_snapshot.pk:
            raise EventLinkageError(
                "Item instance does not belong to the event question snapshot."
            )


def _event_matches(existing, values) -> bool:
    comparable_fields = (
        "event_domain",
        "event_type",
        "event_version",
        "envelope_version",
        "actor_source",
        "session_id",
        "student_id",
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


def _resolve_idempotent_replay(*, client_instance_id, client_sequence_no, values, requested_event_id=None):
    if client_instance_id is None:
        return None
    existing = AssessmentEvent.objects.filter(
        client_instance_id=client_instance_id,
        client_sequence_no=client_sequence_no,
    ).first()
    if existing is None:
        existing = AssessmentEvent.objects.filter(event_id=values['event_id']).first()
        if existing is None:
            return None
    if requested_event_id is not None and str(existing.event_id) != str(requested_event_id):
        raise EventIdempotencyConflict(existing.event_id)
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
    event_id=None,
    envelope_version=2,
    version=None,
    domain=None,
    actor=None,
    server_ingest_sequence_no=None,
    session_lock_held=False,
) -> AssessmentEvent:
    # v2 envelope names are accepted alongside the historical names.
    if version is not None:
        event_version = version
    if domain is not None:
        event_domain = domain
    if actor is not None:
        actor_source = actor
    received_at = timezone.now()
    validate_event_payload(event_type, event_version, payload)
    _register_schema_artifact(event_type, event_version)
    if (client_instance_id is None) != (client_sequence_no is None):
        raise ValueError(
            "client_instance_id and client_sequence_no must be supplied together."
        )
    if attempt is not None and assignment is None:
        assignment = attempt.assignment
    if assignment is not None and attempt is None:
        try:
            attempt = assignment.attempt
        except ObjectDoesNotExist:
            attempt = None
    if assignment is not None and question_snapshot is None:
        question_snapshot = assignment.snapshot
    if assignment is not None and item_instance is None:
        item_instance = assignment.item_instance

    _validate_linkage(
        session=session,
        assignment=assignment,
        attempt=attempt,
        question_snapshot=question_snapshot,
        item_instance=item_instance,
    )
    validate_event_invariants(
        event_type=event_type,
        event_domain=event_domain,
        actor_source=actor_source,
        capture_mode=capture_mode,
        payload=payload,
        session=session,
        assignment=assignment,
        attempt=attempt,
        question_snapshot=question_snapshot,
        item_instance=item_instance,
    )
    # State-chain continuity is a coverage conclusion, not an ingestion gate.
    # A legal observation after a missing event must remain appendable so the
    # gap can be reported rather than erasing everything after it.

    normalized_flags = _deduplicate_quality_flags(quality_flags)
    estimated_clock_offset_ms = None
    estimated_rtt_ms = None
    estimated_occurred_at_utc = None
    if time_sync is not None and (
        client_instance_id is None
        or time_sync.client_instance_id != client_instance_id
    ):
        raise EventLinkageError(
            "Time sync sample does not belong to the event client instance."
        )
    if time_sync is not None and (
        time_sync.session_id is not None
        and time_sync.session_id != session.pk
    ):
        raise EventLinkageError(
            "Time sync sample does not belong to the event session."
        )
    if (
        time_sync is not None
        and client_time_origin is not None
        and time_sync.client_time_origin != client_time_origin
    ):
        raise EventLinkageError(
            "Time sync sample does not belong to the event time origin."
        )
    monotonic_delta_ms = None
    if time_sync is not None and client_monotonic_ms is not None:
        monotonic_delta_ms = abs(
            client_monotonic_ms - time_sync.client_monotonic_ms
        )
    sync_is_valid = (
        time_sync is not None
        and time_sync.sync_quality == ClientTimeSync.SyncQuality.VALID
        and client_time_origin is not None
        and client_monotonic_ms is not None
        and monotonic_delta_ms <= TIME_SYNC_VALIDITY_WINDOW_MS
    )
    if sync_is_valid:
        estimated_clock_offset_ms = time_sync.estimated_clock_offset_ms
        estimated_rtt_ms = time_sync.estimated_rtt_ms
        if client_wall_time_utc is not None:
            estimated_occurred_at_utc = client_wall_time_utc + timedelta(
                milliseconds=estimated_clock_offset_ms
            )
    else:
        time_sync = None
        if client_wall_time_utc is not None and "clock_unsynchronized" not in normalized_flags:
            normalized_flags.append("clock_unsynchronized")

    interval_quality = "unknown"
    if client_instance_id is not None and client_sequence_no is not None:
        previous = AssessmentEvent.objects.filter(
            session_id=session.pk,
            client_instance_id=client_instance_id,
            client_sequence_no__lt=client_sequence_no,
        ).order_by("-client_sequence_no").first()
        if (
            previous is not None
            and client_monotonic_ms is not None
            and previous.client_monotonic_ms is not None
            and client_monotonic_ms < previous.client_monotonic_ms
        ):
            raise ValueError(
                "client_monotonic_ms must not move backwards within a client instance."
            )
        if previous is not None:
            interval_quality = "synchronized_estimate" if sync_is_valid else "exact_monotonic"
        elif AssessmentEvent.objects.filter(session_id=session.pk).exists():
            interval_quality = "degraded"
    elif sync_is_valid:
        interval_quality = "synchronized_estimate"

    values = {
        "event_id": event_id or __import__("uuid").uuid4(),
        "event_domain": event_domain,
        "event_type": event_type,
        "event_version": event_version,
        "recovery_round": session.recovery_round,
        "envelope_version": envelope_version,
        "actor_source": actor_source,
        "session_id": session.pk,
        "student_id": session.student_id,
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
        "interval_quality": interval_quality,
        "capture_mode": capture_mode,
        "quality_flags": normalized_flags,
        "payload": payload,
        "app_version": app_version,
    }

    with transaction.atomic():
        locked_session = None
        if not session_lock_held:
            locked_session = ExperimentSession.objects.select_for_update().get(
                pk=session.pk
            )
        validate_event_invariants(
            event_type=event_type,
            event_domain=event_domain,
            actor_source=actor_source,
            capture_mode=capture_mode,
            payload=payload,
            session=session,
            assignment=assignment,
            attempt=attempt,
            question_snapshot=question_snapshot,
            item_instance=item_instance,
        )
        replay = _resolve_idempotent_replay(
            client_instance_id=client_instance_id,
            client_sequence_no=client_sequence_no,
            values=values,
            requested_event_id=event_id,
        )
        if replay is not None:
            return replay

        if server_ingest_sequence_no is None:
            next_ingest_sequence = locked_session.next_ingest_sequence_no
            locked_session.next_ingest_sequence_no += 1
            locked_session.save(update_fields=["next_ingest_sequence_no"])
        else:
            next_ingest_sequence = server_ingest_sequence_no
        event = AssessmentEvent(
            **values,
            server_ingest_sequence_no=next_ingest_sequence,
            server_received_at=received_at,
            server_committed_at=timezone.now(),
        )
        event.full_clean(validate_unique=False, validate_constraints=False)
        try:
            with transaction.atomic():
                with _assessment_event_append_scope():
                    event.save(force_insert=True)
        except IntegrityError:
            replay = _resolve_idempotent_replay(
                client_instance_id=client_instance_id,
                client_sequence_no=client_sequence_no,
                values=values,
                requested_event_id=event_id,
            )
            if replay is not None:
                return replay
            raise
        terminal_statuses = {
            ExperimentSession.Status.COMPLETED,
            ExperimentSession.Status.EXPIRED,
        }
        effective_status = (
            locked_session.status if locked_session is not None else session.status
        )
        if effective_status in terminal_statuses:
            from apps.analytics.research_materialization_jobs import (
                schedule_session_materialization,
            )

            schedule_session_materialization(session.pk)
        return event
