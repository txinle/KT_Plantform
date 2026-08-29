from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.assessment.models import (
    AssessmentEvent,
    ClientCaptureCheckpoint,
    ClientTimeSync,
    ExperimentSession,
    GradeRecord,
)
from apps.assessment.response_snapshot_services import canonical_response_state

from .models import (
    RecordingCoverageAttempt,
    RecordingCoverageRun,
    RecordingCoverageSession,
)


COVERAGE_VERSION = "coverage-v1"
ALGORITHM_VERSION = "recording-coverage-v1"
COVERAGE_PARAMETERS = {
    "terminal_grace_seconds": 300,
    "time_sync_interval_seconds": 900,
    "time_sync_validity_seconds": 1200,
    "active_time_idle_threshold_seconds": [30, 60, 120],
    "heartbeat_resets_active_time": False,
}


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _hash(value):
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _code_version():
    configured = os.getenv("APP_GIT_COMMIT_SHA", "").strip()
    if configured:
        return configured
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode != 0
        return f"{commit}-dirty" if dirty else commit
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _missing_ranges(sequence_numbers):
    values = sorted(set(sequence_numbers))
    if not values:
        return []
    missing = []
    range_start = None
    prior = None
    for sequence_no in range(1, values[-1] + 1):
        if sequence_no not in values:
            if range_start is None:
                range_start = sequence_no
            prior = sequence_no
        elif range_start is not None:
            missing.append([range_start, prior])
            range_start = None
            prior = None
    if range_start is not None:
        missing.append([range_start, prior])
    return missing


def _has_derived_sync(event, syncs):
    if event.estimated_occurred_at_utc is not None:
        return True
    if (
        event.client_instance_id is None
        or event.client_time_origin is None
        or event.client_monotonic_ms is None
    ):
        return False
    return any(
        sync.sync_quality == ClientTimeSync.SyncQuality.VALID
        and sync.estimated_clock_offset_ms is not None
        and sync.client_instance_id == event.client_instance_id
        and sync.client_time_origin == event.client_time_origin
        and abs(sync.client_monotonic_ms - event.client_monotonic_ms)
        <= COVERAGE_PARAMETERS["time_sync_validity_seconds"] * 1000
        for sync in syncs
    )


def _derived_event_time(event, syncs):
    estimated = getattr(event, "estimated_occurred_at_utc", None)
    if estimated is not None:
        return estimated
    if (
        event.client_instance_id is None
        or event.client_time_origin is None
        or event.client_monotonic_ms is None
    ):
        return None
    candidates = [
        sync
        for sync in syncs
        if sync.sync_quality == ClientTimeSync.SyncQuality.VALID
        and sync.estimated_clock_offset_ms is not None
        and sync.client_instance_id == event.client_instance_id
        and sync.client_time_origin == event.client_time_origin
        and abs(sync.client_monotonic_ms - event.client_monotonic_ms)
        <= COVERAGE_PARAMETERS["time_sync_validity_seconds"] * 1000
    ]
    if not candidates:
        return None
    sample = min(
        candidates,
        key=lambda sync: abs(sync.client_monotonic_ms - event.client_monotonic_ms),
    )
    return (
        sample.client_wall_time_utc
        + timedelta(milliseconds=sample.estimated_clock_offset_ms)
        + timedelta(
            milliseconds=float(
                event.client_monotonic_ms - sample.client_monotonic_ms
            )
        )
    )


def _event_time_quality(events, syncs=()):
    if not events:
        return "unknown"
    client_events = [
        event
        for event in events
        if event.client_instance_id is not None
    ]
    if not client_events:
        return "server_only" if any(event.server_received_at for event in events) else "unknown"
    origins_by_instance = defaultdict(set)
    for event in client_events:
        if event.client_instance_id and event.client_time_origin:
            origins_by_instance[str(event.client_instance_id)].add(
                event.client_time_origin.isoformat()
            )
    if len(origins_by_instance) == 1 and all(
        len(origins) == 1 for origins in origins_by_instance.values()
    ) and all(event.client_monotonic_ms is not None for event in client_events):
        return "exact_monotonic"
    if all(_has_derived_sync(event, syncs) for event in client_events):
        return "synchronized_estimate"
    if any(event.client_wall_time_utc for event in client_events):
        return "client_wall_unsynchronized"
    if any(event.server_received_at for event in events):
        return "server_only"
    return "unknown"


def _eligibility(
    *, usable, missing_reasons, time_quality, run_id, status=None
):
    return {
        "status": status or ("complete" if usable else "insufficient"),
        "usable": usable,
        "missing_reasons": sorted(set(missing_reasons)),
        "time_quality": time_quality,
        "coverage_run_id": str(run_id),
    }


def _ordered_response_events(events, syncs=()):
    response_events = [
        event for event in events if event.event_type == "response_state_changed"
    ]
    instance_ids = {
        str(event.client_instance_id)
        for event in response_events
        if event.client_instance_id is not None
    }
    derived_times = {
        id(event): _derived_event_time(event, syncs)
        for event in response_events
    }
    cross_instance_uncertain = (
        len(instance_ids) > 1
        and not all(derived_times[id(event)] is not None for event in response_events)
    )
    if len(instance_ids) > 1 and not cross_instance_uncertain:
        return (
            sorted(
                response_events,
                key=lambda event: (
                    derived_times[id(event)],
                    event.server_ingest_sequence_no,
                ),
            ),
            False,
        )

    # Preserve cross-instance receive positions while repairing the order
    # inside each client instance from the primary sequence evidence.  This
    # avoids turning an offline late arrival into a false chain break.
    ordered = sorted(
        response_events,
        key=lambda event: (
            event.server_ingest_sequence_no,
            str(getattr(event, "event_id", "")),
        ),
    )
    positions_by_instance = defaultdict(list)
    for index, event in enumerate(ordered):
        positions_by_instance[str(event.client_instance_id)].append(index)
    for positions in positions_by_instance.values():
        members = sorted(
            (ordered[index] for index in positions),
            key=lambda event: (
                getattr(event, "client_sequence_no", None)
                if getattr(event, "client_sequence_no", None) is not None
                else event.server_ingest_sequence_no,
                event.server_ingest_sequence_no,
            ),
        )
        for index, event in zip(positions, members):
            ordered[index] = event
    return ordered, cross_instance_uncertain


def _response_chain(events, syncs=()):
    response_events, cross_instance_uncertain = _ordered_response_events(
        events, syncs
    )
    if not response_events:
        return "insufficient", ["missing_response_state"]
    reasons = ["cross_instance_order_uncertain"] if cross_instance_uncertain else []
    prior_after = None
    for index, event in enumerate(response_events):
        before = event.payload.get("response_state_before", "__missing__")
        after = event.payload.get("response_state_after", "__missing__")
        if before == "__missing__" or after == "__missing__":
            reasons.append("response_chain_break")
            continue
        if index == 0 and before is not None:
            reasons.append("response_chain_break")
        if index and before != prior_after:
            reasons.append("response_chain_break")
        prior_after = after
    return ("partial", reasons) if reasons else ("complete", [])


def _exact_delta_ms(left, right):
    if not (
        left.client_instance_id
        and left.client_instance_id == right.client_instance_id
        and left.client_time_origin
        and left.client_time_origin == right.client_time_origin
        and left.client_monotonic_ms is not None
        and right.client_monotonic_ms is not None
    ):
        return None
    return max(0, int(right.client_monotonic_ms - left.client_monotonic_ms))


def _derived_features(
    events, *, run_id=None, input_status="partial", time_quality="unknown",
    missing_reasons=(), syncs=()
):
    ordered = sorted(events, key=lambda event: event.server_ingest_sequence_no)
    rendered_events = [event for event in ordered if event.event_type == "item_rendered"]
    rendered = rendered_events[0] if rendered_events else None
    responses, _ = _ordered_response_events(ordered, syncs)
    entered = [event for event in ordered if event.event_type == "item_entered"]
    left_events = [event for event in ordered if event.event_type == "item_left"]
    left = left_events[-1] if left_events else None
    requested = next((event for event in ordered if event.event_type == "response_commit_requested"), None)
    features = {
        "derivation_version": "coverage-v1",
        "parameter_hash": _hash(COVERAGE_PARAMETERS),
        "coverage_run_id": str(run_id) if run_id else None,
        "input_status": input_status,
        "time_quality": time_quality,
        "missing_reason": sorted(set(missing_reasons)),
        "revision_count": max(0, len(responses) - 1) if responses else None,
        "first_response_latency_ms": _exact_delta_ms(rendered, responses[0]) if rendered and responses else None,
        "revision_latency_ms": _exact_delta_ms(responses[0], responses[-1]) if len(responses) > 1 else None,
        "post_revision_time_ms": _exact_delta_ms(responses[-1], requested) if responses and requested else None,
        "visit_duration_ms": _exact_delta_ms(rendered, left) if rendered and left else None,
        "state_holding_time_ms": [
            _exact_delta_ms(prior, current)
            for prior, current in zip(responses, [*responses[1:], requested])
            if current is not None
        ] if responses else None,
        "return_count": max(0, max(len(entered), len(rendered_events)) - 1),
        "return_latency_ms": (
            _exact_delta_ms(left_events[0], entered[1])
            if left_events and len(entered) > 1 else None
        ),
        "wall_clock_item_time_ms": (
            _exact_delta_ms(rendered, left or requested)
            if rendered and (left or requested) else None
        ),
    }
    for threshold in (30, 60, 120):
        has_visibility_baseline = any(
            event.event_type in {"document_visible", "document_hidden"}
            for event in ordered
        )
        has_focus_baseline = any(
            event.event_type in {"window_focused", "window_blurred"}
            for event in ordered
        )
        if not has_visibility_baseline or not has_focus_baseline:
            features[f"active_response_time_ms_{threshold}s"] = None
            continue
        activity_boundaries = [
            event for event in ordered if event.event_type != "activity_heartbeat"
        ]
        active = 0
        visible = None
        focused = None
        for prior, current in zip(activity_boundaries, activity_boundaries[1:]):
            if prior.event_type == "document_hidden":
                visible = False
            elif prior.event_type == "document_visible":
                visible = True
            elif prior.event_type == "window_blurred":
                focused = False
            elif prior.event_type == "window_focused":
                focused = True
            delta = _exact_delta_ms(prior, current)
            if visible is True and focused is True and delta is not None:
                active += min(delta, threshold * 1000)
        features[f"active_response_time_ms_{threshold}s"] = active
    return features


def _authoritative_mismatch_reasons(attempt, events):
    """Return contradictions among authoritative submission/snapshot/event facts.

    Missing client evidence remains partial.  This function is deliberately
    limited to contradictions in server-authoritative records, which are the
    only defects allowed to invalidate the outcome chain.
    """
    submission = getattr(attempt, "submission_event", None)
    committed = [event for event in events if event.event_type == "response_committed"]
    reasons = []
    if submission is None:
        if committed:
            reasons.append("submission_chain_mismatch")
        return reasons

    if attempt.final_response_snapshot_id != submission.final_response_snapshot_id:
        reasons.append("final_snapshot_mismatch")
    snapshot = submission.final_response_snapshot
    if snapshot is not None:
        state = snapshot.state_json
        expected = canonical_response_state(
            attempt.assignment.snapshot.question_type,
            submission.final_answer,
        )
        if (
            not isinstance(state, dict)
            or state.get("question_type") != attempt.assignment.snapshot.question_type
            or state.get("value") != expected
        ):
            reasons.append("final_snapshot_mismatch")
    for event in committed:
        if (
            str(event.payload.get("submission_id", "")) != str(submission.pk)
            or str(event.payload.get("submission_token", ""))
            != str(submission.submission_token)
            or str(event.payload.get("final_response_snapshot_id") or "")
            != str(submission.final_response_snapshot_id or "")
        ):
            reasons.append("submission_chain_mismatch")
    return sorted(set(reasons))


def _attempt_result(*, run, attempt, events, session_reasons, gap_count, syncs):
    types = {event.event_type for event in events}
    response_status, response_reasons = _response_chain(events, syncs)
    reasons = list(response_reasons)
    enter_status = "complete" if "item_entered" in types else "partial"
    render_status = "complete" if "item_rendered" in types else "partial"
    leave_status = "complete" if "item_left" in types else "partial"
    if enter_status != "complete":
        reasons.append("missing_item_entered")
    if render_status != "complete":
        reasons.append("missing_item_rendered")
    if leave_status != "complete":
        reasons.append("missing_item_left")
    submission = getattr(attempt, "submission_event", None)
    if submission is not None and submission.final_response_snapshot_id is None:
        reasons.append("missing_final_snapshot")
    time_quality = _event_time_quality(events, syncs)
    if time_quality in {"client_wall_unsynchronized", "server_only", "unknown"}:
        reasons.append("time_sync_unavailable")
    reasons.extend(
        reason
        for reason in session_reasons
        if reason in {"sequence_gap", "tail_gap", "tail_unknown"}
    )

    authoritative_reasons = _authoritative_mismatch_reasons(attempt, events)
    reasons.extend(authoritative_reasons)
    overall = "invalid" if authoritative_reasons else ("complete" if not reasons else "partial")
    if not events and not authoritative_reasons:
        overall = "insufficient"
    if "legacy_contract" in session_reasons:
        reasons.append("legacy_contract")
        overall = "legacy_unknown"
    response_count = sum(
        event.event_type == "response_state_changed" for event in events
    )
    effective_grade_exists = bool(
        submission
        and GradeRecord.objects.filter(
            submission=submission,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        ).exists()
    )
    features = _derived_features(
        events,
        run_id=run.pk,
        input_status=overall,
        time_quality=time_quality,
        missing_reasons=reasons,
        syncs=syncs,
    )
    eligibility = {
        "outcome": _eligibility(
            usable=submission is not None and effective_grade_exists,
            missing_reasons=(
                []
                if submission is not None and effective_grade_exists
                else ["submission_chain_mismatch"]
            ),
            time_quality="server_only",
            run_id=run.pk,
            status=(
                "invalid"
                if authoritative_reasons
                else (
                    "complete"
                    if submission is not None and effective_grade_exists
                    else "insufficient"
                )
            ),
        ),
        "first_response": _eligibility(
            usable=response_count > 0,
            missing_reasons=[] if response_count else ["missing_response_state"],
            time_quality=time_quality,
            run_id=run.pk,
        ),
        "revision": _eligibility(
            # A closed, continuous chain with no revision is still a usable
            # zero.  Requiring >1 states would turn a measured zero into
            # missing data.
            usable=response_status == "complete" and response_count > 0,
            missing_reasons=response_reasons or (["missing_response_state"] if response_count == 0 else []),
            time_quality=time_quality,
            run_id=run.pk,
            status=(
                "complete"
                if response_status == "complete" and response_count > 0
                else response_status
            ),
        ),
        "final_response": _eligibility(
            usable=(
                submission is not None
                and submission.final_response_snapshot_id is not None
                and not authoritative_reasons
            ),
            missing_reasons=[
                reason for reason in reasons
                if reason in {"missing_final_snapshot", "final_snapshot_mismatch"}
            ],
            time_quality="server_only",
            run_id=run.pk,
        ),
        "decision_time": _eligibility(
            usable=features["first_response_latency_ms"] is not None,
            missing_reasons=([] if features["first_response_latency_ms"] is not None else ["time_sync_unavailable"]),
            time_quality=time_quality,
            run_id=run.pk,
        ),
        "revision_time": _eligibility(
            usable=(response_count > 1 and features["revision_latency_ms"] is not None),
            missing_reasons=(
                response_reasons
                if response_count > 1
                else []
            ),
            time_quality=time_quality,
            run_id=run.pk,
            status=(
                "not_applicable"
                if response_count <= 1
                else (
                    "complete"
                    if features["revision_latency_ms"] is not None
                    else "partial"
                )
            ),
        ),
        "navigation": _eligibility(
            usable=(
                enter_status == "complete"
                and render_status == "complete"
                and leave_status == "complete"
            ),
            missing_reasons=[
                reason
                for reason in (
                    "missing_item_entered",
                    "missing_item_rendered",
                    "missing_item_left",
                )
                if reason in reasons
            ],
            time_quality=time_quality,
            run_id=run.pk,
        ),
        "return_behavior": _eligibility(
            usable=(
                enter_status == "complete"
                and render_status == "complete"
                and leave_status == "complete"
            ),
            missing_reasons=[
                reason
                for reason in (
                    "missing_item_entered",
                    "missing_item_rendered",
                    "missing_item_left",
                )
                if reason in reasons
            ],
            time_quality=time_quality,
            run_id=run.pk,
        ),
        "active_time": _eligibility(
            usable=features["active_response_time_ms_60s"] is not None,
            missing_reasons=([] if features["active_response_time_ms_60s"] is not None else ["telemetry_baseline_missing"]),
            time_quality=time_quality,
            run_id=run.pk,
        ),
        "text_editing": _eligibility(
            usable=(
                attempt.assignment.snapshot.question_type == "short_answer"
                and any(event.event_type == "text_edit_observed" for event in events)
                and attempt.final_response_snapshot_id is not None
            ),
            missing_reasons=(
                []
                if attempt.assignment.snapshot.question_type != "short_answer"
                or (
                    any(event.event_type == "text_edit_observed" for event in events)
                    and attempt.final_response_snapshot_id is not None
                )
                else ["missing_response_state"]
            ),
            time_quality=time_quality,
            run_id=run.pk,
            status=(
                "not_applicable"
                if attempt.assignment.snapshot.question_type != "short_answer"
                else (
                    "complete"
                    if any(event.event_type == "text_edit_observed" for event in events)
                    and attempt.final_response_snapshot_id is not None
                    else "insufficient"
                )
            ),
        ),
        "response_replay": _eligibility(
            usable=overall == "complete",
            missing_reasons=reasons,
            time_quality=time_quality,
            run_id=run.pk,
            status=overall,
        ),
    }
    return RecordingCoverageAttempt.objects.create(
        run=run,
        attempt=attempt,
        overall_status=overall,
        enter_status=enter_status,
        render_status=render_status,
        leave_status=leave_status,
        response_chain_status=response_status,
        final_state_status=(
            "invalid"
            if "final_snapshot_mismatch" in authoritative_reasons
            else ("complete" if attempt.final_response_snapshot_id else "partial")
        ),
        time_quality=time_quality,
        response_state_count=response_count,
        sequence_gap_count=gap_count,
        missing_reasons=sorted(set(reasons)),
        analysis_eligibility=eligibility,
        derived_features=features,
    )


def _materialize_session_result(
    *, run, session, events, checkpoints, syncs, attempts
):
    by_instance = defaultdict(list)
    for event in events:
        if event.client_instance_id and event.client_sequence_no is not None:
            by_instance[str(event.client_instance_id)].append(event.client_sequence_no)

    missing_ranges_by_instance = defaultdict(set)
    for instance_id, sequence_numbers in by_instance.items():
        missing_ranges_by_instance[instance_id].update(
            tuple(value) for value in _missing_ranges(sequence_numbers)
        )

    latest_checkpoint_by_instance = {}
    for checkpoint in checkpoints:
        latest_checkpoint_by_instance[str(checkpoint.client_instance_id)] = checkpoint
    tail_gap = False
    dead_letter = False
    persistent_outbox_unavailable = False
    for instance_id, checkpoint in latest_checkpoint_by_instance.items():
        received_max = max(by_instance.get(instance_id, [0]))
        if (
            checkpoint.highest_emitted_sequence_no > received_max
            or checkpoint.highest_contiguous_acked_sequence_no
            < checkpoint.highest_emitted_sequence_no
            or checkpoint.pending_event_count
            or checkpoint.failed_event_count
        ):
            tail_gap = True
        if checkpoint.missing_local_sequence_ranges:
            missing_ranges_by_instance[instance_id].update(
                tuple(value)
                for value in checkpoint.missing_local_sequence_ranges
            )
            tail_gap = True
        if checkpoint.failed_event_count:
            dead_letter = True
        if "persistent_outbox_unavailable" in checkpoint.quality_flags:
            persistent_outbox_unavailable = True
    missing_checkpoint_instances = set(by_instance) - set(latest_checkpoint_by_instance)
    tail_unknown = bool(missing_checkpoint_instances)
    all_missing_ranges = [
        list(value)
        for instance_id in sorted(missing_ranges_by_instance)
        for value in sorted(missing_ranges_by_instance[instance_id])
    ]
    sequence_gap_count = sum(
        end - start + 1 for start, end in all_missing_ranges
    )

    events_by_attempt = defaultdict(list)
    for event in events:
        if event.attempt_id:
            events_by_attempt[event.attempt_id].append(event)
    authoritative_reasons = sorted(
        {
            reason
            for attempt in attempts
            for reason in _authoritative_mismatch_reasons(
                attempt, events_by_attempt.get(attempt.pk, [])
            )
        }
    )

    reasons = []
    event_types = {event.event_type for event in events}
    terminal_event_type = {
        ExperimentSession.Status.COMPLETED: "session_completed",
        ExperimentSession.Status.EXPIRED: "session_expired",
        ExperimentSession.Status.INVALIDATED: "session_invalidated",
    }.get(session.status)
    lifecycle_complete = "session_started" in event_types
    if "session_started" not in event_types:
        reasons.append("missing_session_started")
    if terminal_event_type and terminal_event_type not in event_types:
        lifecycle_complete = False
        reasons.append("missing_session_terminal")
    elif session.status == ExperimentSession.Status.IN_PROGRESS:
        lifecycle_complete = False

    has_visibility_baseline = bool(
        event_types & {"document_visible", "document_hidden"}
    )
    has_focus_baseline = bool(
        event_types & {"window_focused", "window_blurred"}
    )
    telemetry_complete = has_visibility_baseline and has_focus_baseline
    if not telemetry_complete:
        reasons.append("telemetry_baseline_missing")
    valid_sync_exists = any(
        sync.sync_quality == ClientTimeSync.SyncQuality.VALID for sync in syncs
    )
    if not valid_sync_exists:
        reasons.append("time_sync_unavailable")
    origins_by_instance = defaultdict(set)
    for event in events:
        if event.client_instance_id and event.client_time_origin:
            origins_by_instance[str(event.client_instance_id)].add(
                event.client_time_origin
            )
    if any(len(origins) > 1 for origins in origins_by_instance.values()):
        reasons.append("time_origin_changed")

    if not session.capture_contract_id:
        overall = "legacy_unknown"
        reasons.append("legacy_contract")
    elif not any(event.actor_source == "client" for event in events):
        overall = "insufficient"
        reasons.extend(["collector_unavailable", "checkpoint_missing"])
    else:
        if sequence_gap_count:
            reasons.append("sequence_gap")
        if tail_gap:
            reasons.append("tail_gap")
        if tail_unknown:
            reasons.extend(["tail_unknown", "checkpoint_missing"])
        if dead_letter:
            reasons.append("dead_letter_event")
        if persistent_outbox_unavailable:
            reasons.append("persistent_outbox_unavailable")
        if "item_entered" not in event_types:
            reasons.append("missing_item_entered")
        if "item_rendered" not in event_types:
            reasons.append("missing_item_rendered")
        if "item_left" not in event_types:
            reasons.append("missing_item_left")
        if (
            "response_state_changed" not in event_types
            and any(
                attempt.final_response_snapshot_id
                or getattr(attempt, "submission_event", None) is not None
                for attempt in attempts
            )
        ):
            reasons.append("missing_response_state")
        if session.status == "in_progress":
            reasons.append("pending_finalization")
        overall = "complete" if not reasons else "partial"
    if authoritative_reasons:
        reasons.extend(authoritative_reasons)
        overall = "invalid"

    time_quality = _event_time_quality(events, syncs)
    session_eligibility = {
        "response_replay": _eligibility(
            usable=overall == "complete",
            missing_reasons=reasons,
            time_quality=time_quality,
            run_id=run.pk,
            status=overall,
        ),
        "active_time": _eligibility(
            usable=(
                overall == "complete"
                and any(event.event_domain == "telemetry" for event in events)
            ),
            missing_reasons=(
                reasons
                if reasons
                else ([] if any(event.event_domain == "telemetry" for event in events) else ["telemetry_baseline_missing"])
            ),
            time_quality=time_quality,
            run_id=run.pk,
            status=("complete" if overall == "complete" else overall),
        ),
    }
    RecordingCoverageSession.objects.create(
        run=run,
        session=session,
        overall_status=overall,
        lifecycle_status=(
            "complete" if lifecycle_complete else "partial"
        ),
        navigation_status=(
            "complete"
            if {"item_entered", "item_rendered", "item_left"}.issubset(event_types)
            else "insufficient"
        ),
        client_sequence_status=(
            "complete" if not sequence_gap_count and not tail_gap and not tail_unknown else "partial"
        ),
        time_sync_status=(
            "complete" if valid_sync_exists else "partial"
        ),
        telemetry_status=(
            "complete" if telemetry_complete else "insufficient"
        ),
        client_instance_count=len(by_instance),
        sequence_gap_count=sequence_gap_count,
        missing_sequence_ranges=all_missing_ranges,
        tail_gap_detected=tail_gap or tail_unknown,
        missing_reasons=sorted(set(reasons)),
        analysis_eligibility=session_eligibility,
    )

    for attempt in attempts:
        _attempt_result(
            run=run,
            attempt=attempt,
            events=events_by_attempt.get(attempt.pk, []),
            session_reasons=reasons,
            gap_count=sequence_gap_count,
            syncs=syncs,
        )


@transaction.atomic
def generate_scope_coverage(
    sessions, *, scope_type="cohort", scope_definition=None, force=False,
    source_cutoff_at=None
):
    sessions = list(sessions)
    session_ids = sorted({session.pk for session in sessions})
    sessions = list(
        ExperimentSession.objects.filter(pk__in=session_ids)
        .select_related("capture_contract")
        .order_by("pk")
    ) if session_ids else []
    if not sessions:
        raise ValueError("Coverage scope must contain at least one session")
    scope_definition = scope_definition or {"session_ids": session_ids}
    source_cutoff_at = source_cutoff_at or timezone.now()

    events = list(
        AssessmentEvent.objects.filter(
            session_id__in=session_ids,
            server_received_at__lte=source_cutoff_at,
        )
        .select_related("attempt", "time_sync")
        .order_by("session_id", "server_ingest_sequence_no", "event_id")
    )
    checkpoints = list(
        ClientCaptureCheckpoint.objects.filter(
            session_id__in=session_ids,
            server_received_at__lte=source_cutoff_at,
        ).order_by(
            "session_id", "server_received_at", "checkpoint_id"
        )
    )
    syncs = list(
        ClientTimeSync.objects.filter(
            session_id__in=session_ids,
            created_at__lte=source_cutoff_at,
        ).order_by(
            "session_id", "client_instance_id", "created_at"
        )
    )
    attempts = list(
        sessions[0].attempts.model.objects.filter(session_id__in=session_ids)
        .select_related(
            "final_response_snapshot",
            "assignment__snapshot",
            "submission_event__final_response_snapshot",
        )
        .order_by("session_id", "pk")
    )
    source_document = {
        "session_ids": session_ids,
        "event_ids": [str(event.event_id) for event in events],
        "checkpoint_ids": [str(checkpoint.checkpoint_id) for checkpoint in checkpoints],
        "time_syncs": [
            {
                "time_sync_id": str(sync.time_sync_id),
                "sync_quality": sync.sync_quality,
                "finalized_at": sync.finalized_at,
            }
            for sync in syncs
        ],
        "event_high_watermark": max(
            (event.server_ingest_sequence_no for event in events), default=0
        ),
    }
    source_hash = _hash(source_document)
    if not force:
        existing = (
            RecordingCoverageRun.objects.filter(
                scope_type=scope_type,
                scope_hash=_hash(scope_definition),
                source_hash=source_hash,
                run_status=RecordingCoverageRun.Status.COMPLETED,
            )
            .order_by("-generated_at")
            .first()
        )
        if existing is not None:
            return existing

    contract_hashes = sorted(
        {
            session.capture_contract.schema_sha256
            for session in sessions
            if session.capture_contract_id
        }
    )
    run = RecordingCoverageRun.objects.create(
        scope_type=scope_type,
        scope_definition=scope_definition,
        scope_hash=_hash(scope_definition),
        coverage_version=COVERAGE_VERSION,
        algorithm_version=ALGORITHM_VERSION,
        code_version=_code_version(),
        parameter_document=COVERAGE_PARAMETERS,
        parameter_hash=_hash(COVERAGE_PARAMETERS),
        source_cutoff_at=source_cutoff_at,
        source_high_water_marks={
            "assessment_event_server_ingest_sequence_no": source_document[
                "event_high_watermark"
            ],
            "client_capture_checkpoint_count": len(checkpoints),
            "client_time_sync_count": len(syncs),
        },
        capture_contract_hashes=contract_hashes,
        source_hash=source_hash,
        run_status=RecordingCoverageRun.Status.COMPLETED,
    )

    events_by_session = defaultdict(list)
    for event in events:
        events_by_session[event.session_id].append(event)
    checkpoints_by_session = defaultdict(list)
    for checkpoint in checkpoints:
        checkpoints_by_session[checkpoint.session_id].append(checkpoint)
    syncs_by_session = defaultdict(list)
    for sync in syncs:
        syncs_by_session[sync.session_id].append(sync)
    attempts_by_session = defaultdict(list)
    for attempt in attempts:
        attempts_by_session[attempt.session_id].append(attempt)
    for session in sessions:
        _materialize_session_result(
            run=run,
            session=session,
            events=events_by_session[session.pk],
            checkpoints=checkpoints_by_session[session.pk],
            syncs=syncs_by_session[session.pk],
            attempts=attempts_by_session[session.pk],
        )
    return run


def generate_session_coverage(session, *, force=False):
    return generate_scope_coverage(
        [session],
        scope_type="session",
        scope_definition={"session_id": session.pk},
        force=force,
    )
