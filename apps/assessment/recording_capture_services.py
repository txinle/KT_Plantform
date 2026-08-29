from __future__ import annotations

import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .event_batch_services import (
    parse_aware_datetime,
    parse_finite_decimal,
    parse_positive_integer,
    parse_uuid,
    require_exact_mapping,
)
from .models import (
    CaptureContractArtifact,
    ClientCaptureCheckpoint,
    ClientTimeSync,
    ExperimentSession,
    validate_quality_flags,
)


CAPTURE_CONTRACT_V1 = {
    "contract_version": 1,
    "requirements": {
        "session_lifecycle": "required",
        "navigation": "required",
        "response_states": "required",
        "telemetry_baseline": "required",
        "time_sync_attempt": "required",
        "text_observation": "conditional_by_item_type",
        "confidence": "conditional_by_experiment",
        "simulation": "conditional_by_experiment",
    },
    "parameters": {
        "stable_state_idle_ms": 800,
        "transition_ack_wait_ms": 1500,
        "terminal_grace_seconds": 300,
        "time_sync_interval_ms": 900000,
        "time_sync_validity_ms": 1200000,
        "active_time_idle_threshold_seconds": [30, 60, 120],
    },
}


def _canonical_hash(document):
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_capture_contract_v1():
    digest = _canonical_hash(CAPTURE_CONTRACT_V1)
    contract, created = CaptureContractArtifact.objects.get_or_create(
        contract_name="capture-v1",
        defaults={
            "contract_version": 1,
            "schema_document": CAPTURE_CONTRACT_V1,
            "schema_sha256": digest,
        },
    )
    if not created and (
        contract.contract_version != 1
        or contract.schema_document != CAPTURE_CONTRACT_V1
        or contract.schema_sha256 != digest
    ):
        raise RuntimeError("capture-v1 contract drift detected")
    return contract


CHECKPOINT_FIELDS = frozenset(
    {
        "checkpoint_id",
        "session_id",
        "client_instance_id",
        "highest_emitted_sequence_no",
        "highest_acked_sequence_no",
        "highest_contiguous_acked_sequence_no",
        "pending_event_count",
        "failed_event_count",
        "missing_local_sequence_ranges",
        "client_monotonic_ms",
        "client_wall_time_utc",
        "client_time_origin",
        "time_sync_id",
        "trigger",
        "quality_flags",
        "app_version",
    }
)


def _nonnegative_integer(value):
    if isinstance(value, bool):
        raise ValidationError("checkpoint counters must be integers")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError("checkpoint counters must be integers") from error
    if result < 0:
        raise ValidationError("checkpoint counters must be nonnegative")
    return result


def _checkpoint_values(*, student, document):
    require_exact_mapping(document, allowed=CHECKPOINT_FIELDS)
    session = ExperimentSession.objects.filter(
        pk=parse_positive_integer(document["session_id"]), student=student
    ).first()
    if session is None:
        raise ValidationError("checkpoint session is not owned by student")
    client_instance_id = parse_uuid(document["client_instance_id"])
    time_sync = None
    if document.get("time_sync_id") is not None:
        time_sync = ClientTimeSync.objects.filter(
            pk=parse_uuid(document["time_sync_id"]),
            session=session,
            client_instance_id=client_instance_id,
        ).first()
        if time_sync is None:
            raise ValidationError("checkpoint time sync is invalid")
    flags = document["quality_flags"]
    validate_quality_flags(flags)
    ranges = document["missing_local_sequence_ranges"]
    if not isinstance(ranges, list) or any(
        not isinstance(item, list)
        or len(item) != 2
        or any(not isinstance(value, int) or value < 1 for value in item)
        or item[0] > item[1]
        for item in ranges
    ):
        raise ValidationError("checkpoint missing ranges are invalid")
    values = {
        "checkpoint_id": parse_uuid(document["checkpoint_id"]),
        "session": session,
        "student": student,
        "client_instance_id": client_instance_id,
        "highest_emitted_sequence_no": _nonnegative_integer(
            document["highest_emitted_sequence_no"]
        ),
        "highest_acked_sequence_no": _nonnegative_integer(
            document["highest_acked_sequence_no"]
        ),
        "highest_contiguous_acked_sequence_no": _nonnegative_integer(
            document["highest_contiguous_acked_sequence_no"]
        ),
        "pending_event_count": _nonnegative_integer(document["pending_event_count"]),
        "failed_event_count": _nonnegative_integer(document["failed_event_count"]),
        "missing_local_sequence_ranges": ranges,
        "client_monotonic_ms": parse_finite_decimal(
            document["client_monotonic_ms"]
        ),
        "client_wall_time_utc": parse_aware_datetime(
            document["client_wall_time_utc"]
        ),
        "client_time_origin": parse_aware_datetime(document["client_time_origin"]),
        "time_sync": time_sync,
        "trigger": document["trigger"],
        "quality_flags": flags,
        "app_version": document["app_version"],
    }
    if values["trigger"] not in ClientCaptureCheckpoint.Trigger.values:
        raise ValidationError("checkpoint trigger is invalid")
    if not isinstance(values["app_version"], str) or len(values["app_version"]) > 64:
        raise ValidationError("checkpoint app version is invalid")
    if not (
        values["highest_contiguous_acked_sequence_no"]
        <= values["highest_acked_sequence_no"]
        <= values["highest_emitted_sequence_no"]
    ):
        raise ValidationError("checkpoint acknowledgement counters are inconsistent")
    return values


@transaction.atomic
def record_client_capture_checkpoint(*, student, document):
    values = _checkpoint_values(student=student, document=document)
    checkpoint_id = values["checkpoint_id"]
    existing = ClientCaptureCheckpoint.objects.filter(pk=checkpoint_id).first()
    if existing is not None:
        comparable = {
            field: getattr(existing, field)
            for field in (
                "session_id",
                "student_id",
                "client_instance_id",
                "highest_emitted_sequence_no",
                "highest_acked_sequence_no",
                "highest_contiguous_acked_sequence_no",
                "pending_event_count",
                "failed_event_count",
                "missing_local_sequence_ranges",
                "client_monotonic_ms",
                "client_wall_time_utc",
                "client_time_origin",
                "time_sync_id",
                "trigger",
                "quality_flags",
                "app_version",
            )
        }
        expected = {
            "session_id": values["session"].pk,
            "student_id": values["student"].pk,
            "client_instance_id": values["client_instance_id"],
            "highest_emitted_sequence_no": values["highest_emitted_sequence_no"],
            "highest_acked_sequence_no": values["highest_acked_sequence_no"],
            "highest_contiguous_acked_sequence_no": values[
                "highest_contiguous_acked_sequence_no"
            ],
            "pending_event_count": values["pending_event_count"],
            "failed_event_count": values["failed_event_count"],
            "missing_local_sequence_ranges": values["missing_local_sequence_ranges"],
            "client_monotonic_ms": values["client_monotonic_ms"],
            "client_wall_time_utc": values["client_wall_time_utc"],
            "client_time_origin": values["client_time_origin"],
            "time_sync_id": values["time_sync"].pk if values["time_sync"] else None,
            "trigger": values["trigger"],
            "quality_flags": values["quality_flags"],
            "app_version": values["app_version"],
        }
        if comparable != expected:
            raise ValidationError("checkpoint idempotency conflict")
        return existing
    try:
        with transaction.atomic():
            return ClientCaptureCheckpoint.objects.create(**values)
    except IntegrityError:
        return record_client_capture_checkpoint(student=student, document=document)
