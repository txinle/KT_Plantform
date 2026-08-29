from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.assessment.event_invariants import (
    ResponseStateChainError,
    validate_response_state_chain,
)
from apps.assessment.response_snapshot_services import canonical_response_state
from apps.experiments.presentation import display_text


class ProcessCompleteness(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class ProcessStep:
    generated_at: datetime
    kind: str
    label: str
    occurred_at: datetime | None
    value: str | None = None
    length_before: int | None = None
    length_after: int | None = None


@dataclass(frozen=True)
class AttemptProcessProjection:
    generated_at: datetime
    question_type: str
    completeness: ProcessCompleteness
    completeness_label: str
    steps: tuple[ProcessStep, ...]
    first_value: str | None
    final_value: str | None
    revision_count: int | None


_CHOICE_TYPES = frozenset({"single_choice", "multiple_choice", "true_false"})
_VALUE_CHAIN_TYPES = frozenset({"numeric", "fill_blank"})
_EDIT_LABELS = {
    "insert": "输入",
    "delete": "删除",
    "replace": "替换",
    "paste": "粘贴",
    "unknown": "编辑",
}


def _event_time(event):
    return event.estimated_occurred_at_utc or event.server_received_at


def _choice_display(question_type, value):
    canonical = canonical_response_state(question_type, value)
    if canonical is None or canonical == []:
        return None
    if question_type == "true_false":
        return "正确" if canonical == "true" else "错误"
    if question_type == "multiple_choice":
        return "、".join(display_text(item) for item in canonical)
    return display_text(canonical)


def _incomplete(generated_at, question_type, final_answer):
    final_value = (
        _choice_display(question_type, final_answer)
        if question_type in _CHOICE_TYPES
        else display_text(final_answer)
    )
    return AttemptProcessProjection(
        generated_at=generated_at,
        question_type=question_type,
        completeness=ProcessCompleteness.INCOMPLETE,
        completeness_label="不完整链",
        steps=(),
        first_value=None,
        final_value=final_value or None,
        revision_count=None,
    )


def _project_choice(
    *, generated_at, attempt_id, question_type, events, final_answer, submitted_at
):
    response_events = tuple(
        sorted(
            (
                event
                for event in events
                if event.event_type == "response_state_changed"
            ),
            key=lambda event: event.server_ingest_sequence_no,
        )
    )
    if (
        not response_events
        or submitted_at is None
        or any(getattr(event, "attempt_id", attempt_id) != attempt_id for event in response_events)
    ):
        return _incomplete(generated_at, question_type, final_answer)
    try:
        validate_response_state_chain(response_events)
    except ResponseStateChainError:
        return _incomplete(generated_at, question_type, final_answer)

    values = []
    steps = []
    for event in response_events:
        payload = event.payload
        if payload.get("question_type") != question_type:
            return _incomplete(generated_at, question_type, final_answer)
        value = _choice_display(question_type, payload.get("response_state_after"))
        if value is None:
            continue
        values.append(value)
        steps.append(
            ProcessStep(
                generated_at=generated_at,
                kind="response",
                label="选择" if question_type != "multiple_choice" else "更新选择",
                occurred_at=_event_time(event),
                value=value,
            )
        )
    final_value = _choice_display(question_type, final_answer)
    if not values or final_value is None or values[-1] != final_value:
        return _incomplete(generated_at, question_type, final_answer)
    steps.append(
        ProcessStep(
            generated_at=generated_at,
            kind="submit",
            label="提交",
            occurred_at=submitted_at,
            value=final_value,
        )
    )
    revision_count = None
    if question_type in {"single_choice", "true_false"}:
        revision_count = sum(
            current != previous for previous, current in zip(values, values[1:])
        )
    return AttemptProcessProjection(
        generated_at=generated_at,
        question_type=question_type,
        completeness=ProcessCompleteness.COMPLETE,
        completeness_label="完整链",
        steps=tuple(steps),
        first_value=values[0],
        final_value=final_value,
        revision_count=revision_count,
    )


def _snapshot_index(snapshots):
    return {str(snapshot.pk): snapshot for snapshot in snapshots}


def _resolve_state_ref(*, ref, snapshot_by_id, attempt_id, question_type):
    if not isinstance(ref, dict):
        return None
    snapshot = snapshot_by_id.get(str(ref.get("snapshot_id", "")))
    if snapshot is None or snapshot.attempt_id != attempt_id:
        return None
    if ref.get("state_hash") != snapshot.state_hash:
        return None
    if ref.get("content_length") != snapshot.content_length:
        return None
    state = snapshot.state_json
    if not isinstance(state, dict) or state.get("question_type") != question_type:
        return None
    return display_text(state.get("value"))


def _project_value_chain(
    *,
    generated_at,
    attempt_id,
    question_type,
    events,
    snapshots,
    final_answer,
    final_snapshot_id,
    submitted_at,
):
    response_events = tuple(
        sorted(
            (
                event
                for event in events
                if event.event_type == "response_state_changed"
            ),
            key=lambda event: event.server_ingest_sequence_no,
        )
    )
    if not response_events or submitted_at is None or final_snapshot_id is None:
        return _incomplete(generated_at, question_type, final_answer)
    if any(getattr(event, "attempt_id", attempt_id) != attempt_id for event in response_events):
        return _incomplete(generated_at, question_type, final_answer)
    if any("response_state_after" not in event.payload for event in response_events):
        return _incomplete(generated_at, question_type, final_answer)
    try:
        validate_response_state_chain(response_events)
    except ResponseStateChainError:
        return _incomplete(generated_at, question_type, final_answer)

    snapshot_by_id = _snapshot_index(snapshots)
    values = []
    steps = []
    last_ref = None
    for event in response_events:
        payload = event.payload
        if payload.get("question_type") != question_type:
            return _incomplete(generated_at, question_type, final_answer)
        ref = payload.get("response_state_after")
        value = _resolve_state_ref(
            ref=ref,
            snapshot_by_id=snapshot_by_id,
            attempt_id=attempt_id,
            question_type=question_type,
        )
        if value is None:
            return _incomplete(generated_at, question_type, final_answer)
        last_ref = ref
        values.append(value)
        steps.append(
            ProcessStep(
                generated_at=generated_at,
                kind="response",
                label="输入结果",
                occurred_at=_event_time(event),
                value=value,
            )
        )
    final_value = display_text(final_answer)
    if (
        str(last_ref.get("snapshot_id")) != str(final_snapshot_id)
        or not values
        or values[-1] != final_value
    ):
        return _incomplete(generated_at, question_type, final_answer)
    steps.append(
        ProcessStep(
            generated_at=generated_at,
            kind="submit",
            label="提交",
            occurred_at=submitted_at,
            value=final_value,
        )
    )
    return AttemptProcessProjection(
        generated_at=generated_at,
        question_type=question_type,
        completeness=ProcessCompleteness.COMPLETE,
        completeness_label="完整链",
        steps=tuple(steps),
        first_value=values[0],
        final_value=final_value,
        revision_count=None,
    )


def _project_long_text(
    *,
    generated_at,
    attempt_id,
    snapshots,
    observations,
    final_answer,
    final_snapshot_id,
    submitted_at,
):
    ordered = tuple(sorted(observations, key=lambda item: item.observed_at))
    snapshot = _snapshot_index(snapshots).get(str(final_snapshot_id))
    final_value = display_text(final_answer)
    if (
        not ordered
        or submitted_at is None
        or snapshot is None
        or snapshot.attempt_id != attempt_id
        or any(observation.attempt_id != attempt_id for observation in ordered)
    ):
        return _incomplete(generated_at, "short_answer", final_answer)
    for previous, current in zip(ordered, ordered[1:]):
        if previous.content_hash_after != current.content_hash_before:
            return _incomplete(generated_at, "short_answer", final_answer)
    state = snapshot.state_json
    if (
        snapshot.content_hash != ordered[-1].content_hash_after
        or snapshot.content_length != ordered[-1].content_length_after
        or not isinstance(state, dict)
        or state.get("question_type") != "short_answer"
        or display_text(state.get("value")) != final_value
    ):
        return _incomplete(generated_at, "short_answer", final_answer)

    steps = tuple(
        ProcessStep(
            generated_at=generated_at,
            kind=observation.edit_operation,
            label=_EDIT_LABELS.get(observation.edit_operation, "编辑"),
            occurred_at=observation.observed_at,
            length_before=observation.content_length_before,
            length_after=observation.content_length_after,
        )
        for observation in ordered
    ) + (
        ProcessStep(
            generated_at=generated_at,
            kind="submit",
            label="提交",
            occurred_at=submitted_at,
            length_after=snapshot.content_length,
        ),
    )
    return AttemptProcessProjection(
        generated_at=generated_at,
        question_type="short_answer",
        completeness=ProcessCompleteness.COMPLETE,
        completeness_label="完整链",
        steps=steps,
        first_value=None,
        final_value=final_value,
        revision_count=None,
    )


def project_attempt_process(
    *,
    generated_at,
    attempt_id,
    question_type,
    events,
    snapshots,
    observations,
    final_answer,
    final_snapshot_id,
    submitted_at,
):
    """Project one attempt into a privacy-bounded teacher-facing process."""
    if question_type in _CHOICE_TYPES:
        return _project_choice(
            generated_at=generated_at,
            attempt_id=attempt_id,
            question_type=question_type,
            events=events,
            final_answer=final_answer,
            submitted_at=submitted_at,
        )
    if question_type in _VALUE_CHAIN_TYPES:
        return _project_value_chain(
            generated_at=generated_at,
            attempt_id=attempt_id,
            question_type=question_type,
            events=events,
            snapshots=snapshots,
            final_answer=final_answer,
            final_snapshot_id=final_snapshot_id,
            submitted_at=submitted_at,
        )
    if question_type == "short_answer":
        return _project_long_text(
            generated_at=generated_at,
            attempt_id=attempt_id,
            snapshots=snapshots,
            observations=observations,
            final_answer=final_answer,
            final_snapshot_id=final_snapshot_id,
            submitted_at=submitted_at,
        )
    return _incomplete(generated_at, question_type, final_answer)
