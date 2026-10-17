"""Five-state data-quality reporting for research records and legacy projections."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from apps.assessment.event_invariants import ResponseStateChainError, validate_response_state_chain
from apps.assessment.models import AssessmentEvent


class EvaluationState(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class QualityReportRow:
    rule: str
    state: EvaluationState
    attempt_id: int | None
    detail: str = ""
    rule_version: str = "v1"
    entity_type: str = "attempt"
    reason_code: str = ""
    source_mode: str = "native"

    @property
    def entity_id(self):
        return self.attempt_id

    @property
    def outcome(self):
        return self.state

    @property
    def evidence_summary(self):
        return self.detail


@dataclass(frozen=True)
class QualityReport:
    rows: tuple[QualityReportRow, ...]

    @property
    def counts(self):
        # A ``Counter`` returns ``0`` for every missing key.  Django template
        # lookup therefore resolves ``counts.items`` as Counter["items"]
        # instead of its mapping method, which makes the quality page fail at
        # render time.  Return an ordinary mapping at the presentation edge.
        observed = Counter(row.state for row in self.rows)
        return {state: observed[state] for state in EvaluationState}


def _legacy_or_missing(events):
    return not events or any(
        event.capture_mode == AssessmentEvent.CaptureMode.LEGACY_PROJECTION
        for event in events
    )


def _source_mode(events):
    if not events:
        return "missing_observation"
    modes = {event.capture_mode for event in events}
    if modes == {AssessmentEvent.CaptureMode.LEGACY_PROJECTION}:
        return "legacy_projection"
    if AssessmentEvent.CaptureMode.LEGACY_PROJECTION in modes:
        return "mixed"
    return "native"


def _identity_row(attempt, events):
    if _legacy_or_missing(events):
        return QualityReportRow(
            "identity", EvaluationState.NOT_EVALUABLE, attempt.pk, "缺少原生事件锚点",
            reason_code="missing_native_event_anchor", source_mode=_source_mode(events),
        )
    valid = all(
        event.session_id == attempt.session_id
        and event.assignment_id == attempt.assignment_id
        and event.question_snapshot_id == attempt.assignment.snapshot_id
        and event.item_instance_id == attempt.assignment.item_instance_id
        for event in events
    )
    return QualityReportRow(
        "identity", EvaluationState.PASS if valid else EvaluationState.FAIL, attempt.pk,
        reason_code="" if valid else "identity_anchor_mismatch", source_mode=_source_mode(events),
    )


def _state_chain_row(attempt, events):
    if _legacy_or_missing(events):
        return QualityReportRow(
            "state_chain",
            EvaluationState.NOT_EVALUABLE,
            attempt.pk,
            "历史投影或无原生状态事件",
            reason_code="missing_native_state_observation", source_mode=_source_mode(events),
        )
    state_events = [
        event for event in events if "response_state_after" in (event.payload or {})
    ]
    if not state_events:
        return QualityReportRow(
            "state_chain", EvaluationState.NOT_APPLICABLE, attempt.pk,
            reason_code="no_response_state_event", source_mode=_source_mode(events),
        )
    try:
        validate_response_state_chain(state_events)
    except ResponseStateChainError as error:
        return QualityReportRow("state_chain", EvaluationState.FAIL, attempt.pk, str(error), reason_code="response_state_chain_mismatch", source_mode=_source_mode(events))
    return QualityReportRow("state_chain", EvaluationState.PASS, attempt.pk, source_mode=_source_mode(events))


def _submission_row(attempt):
    if attempt.status != attempt.Status.SUBMITTED:
        return QualityReportRow(
            "submission", EvaluationState.NOT_APPLICABLE, attempt.pk,
            reason_code="attempt_not_submitted",
        )
    submission = getattr(attempt, "submission_event", None)
    if submission is None:
        return QualityReportRow(
            "submission", EvaluationState.FAIL, attempt.pk, "已提交作答缺少 Submission",
            reason_code="missing_submission",
        )
    valid = (
        submission.final_response_snapshot_id == attempt.final_response_snapshot_id
        and attempt.final_response_snapshot_id == attempt.current_response_snapshot_id
    )
    return QualityReportRow(
        "submission", EvaluationState.PASS if valid else EvaluationState.FAIL, attempt.pk,
        reason_code="" if valid else "submission_snapshot_mismatch",
    )


def _ordering_row(attempt, events):
    if _legacy_or_missing(events):
        return QualityReportRow(
            "ordering_time",
            EvaluationState.NOT_EVALUABLE,
            attempt.pk,
            "历史投影或缺少客户端顺序记录",
            reason_code="missing_client_sequence", source_mode=_source_mode(events),
        )
    client_events = [
        event
        for event in events
        if event.client_instance_id is not None and event.client_sequence_no is not None
    ]
    if not client_events:
        return QualityReportRow(
            "ordering_time", EvaluationState.NOT_EVALUABLE, attempt.pk,
            reason_code="missing_client_sequence", source_mode=_source_mode(events),
        )
    by_client = {}
    for event in client_events:
        by_client.setdefault(event.client_instance_id, []).append(event)
    for group in by_client.values():
        sequence_numbers = [event.client_sequence_no for event in group]
        if sequence_numbers != sorted(sequence_numbers) or len(sequence_numbers) != len(
            set(sequence_numbers)
        ):
            return QualityReportRow(
                "ordering_time", EvaluationState.FAIL, attempt.pk, "客户端序号不严格递增",
                reason_code="client_sequence_not_strict", source_mode=_source_mode(events),
            )
    degraded = any(
        event.interval_quality
        in {AssessmentEvent.IntervalQuality.DEGRADED, AssessmentEvent.IntervalQuality.UNKNOWN}
        for event in client_events
    )
    return QualityReportRow(
        "ordering_time", EvaluationState.WARN if degraded else EvaluationState.PASS, attempt.pk,
        "时间间隔存在降级估计" if degraded else "",
        reason_code="interval_degraded" if degraded else "", source_mode=_source_mode(events),
    )


def _provenance_row(attempt):
    return QualityReportRow(
        "provenance", EvaluationState.NOT_APPLICABLE, attempt.pk, "导出时评估",
        reason_code="export_scoped_rule", source_mode="not_yet_exported",
    )


def build_quality_report(scope):
    rows = []
    attempts = list(
        scope.attempts().select_related(
            "assignment__snapshot", "assignment__item_instance", "submission_event"
        )
    )
    events_by_attempt = {}
    for event in scope.events().filter(attempt__isnull=False).order_by(
        "attempt_id", "server_ingest_sequence_no"
    ):
        events_by_attempt.setdefault(event.attempt_id, []).append(event)
    for attempt in attempts:
        events = events_by_attempt.get(attempt.pk, [])
        rows.extend(
            (
                _identity_row(attempt, events),
                _state_chain_row(attempt, events),
                _submission_row(attempt),
                _ordering_row(attempt, events),
                _provenance_row(attempt),
            )
        )
    return QualityReport(rows=tuple(rows))
