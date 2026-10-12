from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.assessment.event_invariants import (
    ResponseStateChainError,
    validate_response_state_chain,
)
from apps.assessment.response_snapshot_services import ResponseState, canonical_response_state
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
    value_before: str | None = None
    value_after: str | None = None
    length_before: int | None = None
    length_after: int | None = None
    time_source: str = "server_received_at"
    interval_ms: int | None = None


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


def order_process_records(records, *, fallback_time_field):
    """Preserve receive positions while restoring each client sequence.

    Cross-instance order remains a Coverage concern.  This helper only applies
    the order that is exact inside one client instance and otherwise leaves the
    server-observed positions unchanged.
    """
    ordered = sorted(
        records,
        key=lambda item: (
            getattr(item, fallback_time_field),
            str(getattr(item, "event_id", getattr(item, "observation_id", ""))),
        ),
    )
    positions_by_instance = {}
    for index, item in enumerate(ordered):
        instance_id = getattr(item, "client_instance_id", None)
        if instance_id is not None:
            positions_by_instance.setdefault(str(instance_id), []).append(index)
    for positions in positions_by_instance.values():
        members = sorted(
            (ordered[index] for index in positions),
            key=lambda item: (
                getattr(item, "client_sequence_no", None)
                if getattr(item, "client_sequence_no", None) is not None
                else getattr(item, fallback_time_field),
                getattr(item, fallback_time_field),
            ),
        )
        for index, item in zip(positions, members):
            ordered[index] = item
    return tuple(ordered)


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


def _state_display(question_type, value):
    if question_type in _CHOICE_TYPES:
        rendered = _choice_display(question_type, value)
    else:
        rendered = display_text(value)
    return rendered or "（空）"


def _capture_state(payload, side, question_type):
    state = payload.get(f"{side}_state")
    if not isinstance(state, dict) or state.get("question_type") != question_type:
        return False, None
    value = state.get("value")
    try:
        canonical = canonical_response_state(question_type, value)
    except (TypeError, ValueError):
        return False, None
    if canonical != value:
        return False, None
    expected_hash = ResponseState(question_type, value).state_hash
    if payload.get(f"{side}_state_hash") != expected_hash:
        return False, None
    return True, value


def _capture_snapshot_matches(snapshot_by_id, snapshot_id, *, attempt_id, state, state_hash):
    if snapshot_id is None:
        return True
    snapshot = snapshot_by_id.get(str(snapshot_id))
    return bool(
        snapshot is not None
        and snapshot.attempt_id == attempt_id
        and snapshot.state_hash == state_hash
        and snapshot.state_json == state
    )


def _capture_v2_projection(
    *,
    generated_at,
    attempt_id,
    question_type,
    events,
    snapshots,
    final_answer,
    final_snapshot_id,
    submitted_at,
    final_response_event_id,
    formal_capture_boundary,
    submission,
):
    incomplete = lambda: AttemptProcessProjection(
        **{
            **_incomplete(generated_at, question_type, final_answer).__dict__,
            "completeness_label": "证据缺口",
        }
    )
    if (
        submitted_at is None
        or submission is None
        or not final_response_event_id
        or not isinstance(formal_capture_boundary, dict)
        or str(formal_capture_boundary.get("active_response_event_id"))
        != str(final_response_event_id)
    ):
        return incomplete()
    try:
        boundary_sequence = int(formal_capture_boundary["server_ingest_sequence_no"])
    except (KeyError, TypeError, ValueError):
        return incomplete()

    transitions = {
        str(event.event_id): event
        for event in events
        if event.event_type == "response_transition"
        and getattr(event, "attempt_id", None) == attempt_id
        and event.server_ingest_sequence_no <= boundary_sequence
    }
    path = []
    visited = set()
    current_id = str(final_response_event_id)
    snapshot_by_id = _snapshot_index(snapshots)
    while current_id:
        if current_id in visited:
            return incomplete()
        visited.add(current_id)
        event = transitions.get(current_id)
        if event is None:
            return incomplete()
        payload = event.payload
        if not isinstance(payload, dict) or payload.get("capture_version") != "capture-v2":
            return incomplete()
        before_valid, before = _capture_state(payload, "before", question_type)
        after_valid, after = _capture_state(payload, "after", question_type)
        if not before_valid or not after_valid:
            return incomplete()
        if payload.get("before_state_hash") == payload.get("after_state_hash"):
            return incomplete()
        if not _capture_snapshot_matches(
            snapshot_by_id,
            payload.get("before_snapshot_id"),
            attempt_id=attempt_id,
            state=payload.get("before_state"),
            state_hash=payload.get("before_state_hash"),
        ) or not _capture_snapshot_matches(
            snapshot_by_id,
            payload.get("after_snapshot_id"),
            attempt_id=attempt_id,
            state=payload.get("after_state"),
            state_hash=payload.get("after_state_hash"),
        ):
            return incomplete()
        path.append((event, before, after))
        parent_id = payload.get("parent_event_id")
        if parent_id is None:
            empty = [] if question_type == "multiple_choice" else (
                "" if question_type in {"short_answer", "fill_blank", "numeric"} else None
            )
            if before != empty:
                return incomplete()
            current_id = ""
        else:
            parent = transitions.get(str(parent_id))
            if (
                parent is None
                or not isinstance(parent.payload, dict)
                or parent.payload.get("after_state_hash")
                != payload.get("before_state_hash")
            ):
                return incomplete()
            current_id = str(parent_id)
    path.reverse()

    final_snapshot = snapshot_by_id.get(str(final_snapshot_id))
    head_after = path[-1][2]
    if (
        final_snapshot is None
        or final_snapshot.attempt_id != attempt_id
        or final_snapshot.state_json != {"question_type": question_type, "value": head_after}
        or final_snapshot.state_hash != ResponseState(question_type, head_after).state_hash
        or canonical_response_state(question_type, final_answer) != head_after
        or submission.attempt_id != attempt_id
        or submission.final_answer != final_answer
        or str(submission.final_response_snapshot_id) != str(final_snapshot_id)
        or submission.committed_at != submitted_at
    ):
        return incomplete()

    commits = [
        event for event in events
        if event.event_type == "response_committed"
        and getattr(event, "attempt_id", None) == attempt_id
    ]
    if len(commits) != 1:
        return incomplete()
    commit = commits[0]
    commit_payload = commit.payload if isinstance(commit.payload, dict) else {}
    if (
        str(commit_payload.get("submission_token")) != str(submission.submission_token)
        or commit_payload.get("submission_id") != submission.pk
        or str(commit_payload.get("final_response_snapshot_id")) != str(final_snapshot_id)
        or commit_payload.get("committed_at") != submitted_at.isoformat()
        or commit.server_ingest_sequence_no <= boundary_sequence
    ):
        return incomplete()

    steps = []
    source_events = []
    anchors = [
        event for event in events
        if event.event_type in {"item_entered", "item_rendered"}
        and getattr(event, "attempt_id", None) == attempt_id
        and event.server_ingest_sequence_no <= path[0][0].server_ingest_sequence_no
        and event.server_ingest_sequence_no <= boundary_sequence
    ]
    if anchors:
        anchor = min(anchors, key=lambda event: event.server_ingest_sequence_no)
        steps.append(ProcessStep(
            generated_at=generated_at,
            kind="item_entered",
            label="进入题目",
            occurred_at=_event_time(anchor),
        ))
        source_events.append(anchor)
    for event, before, after in path:
        before_display = _state_display(question_type, before)
        after_display = _state_display(question_type, after)
        label = "清空答案" if after_display == "（空）" else (
            "填写答案" if before_display == "（空）" else "修改答案"
        )
        steps.append(ProcessStep(
            generated_at=generated_at,
            kind="response",
            label=label,
            occurred_at=_event_time(event),
            value=after_display,
            value_before=before_display,
            value_after=after_display,
            length_before=len(before) if isinstance(before, str) and question_type in {"short_answer", "fill_blank", "numeric"} else None,
            length_after=len(after) if isinstance(after, str) and question_type in {"short_answer", "fill_blank", "numeric"} else None,
        ))
        source_events.append(event)
    final_value = _state_display(question_type, head_after)
    steps.append(ProcessStep(
        generated_at=generated_at,
        kind="submit",
        label="正式提交",
        occurred_at=submitted_at,
        value=final_value,
    ))
    source_events.append(commit)

    timed_steps = []
    previous_event = None
    for step, source_event in zip(steps, source_events):
        interval_ms = None
        if (
            previous_event is not None
            and source_event.client_instance_id
            and source_event.client_instance_id == previous_event.client_instance_id
            and source_event.client_time_origin == previous_event.client_time_origin
            and source_event.client_monotonic_ms is not None
            and previous_event.client_monotonic_ms is not None
        ):
            delta = source_event.client_monotonic_ms - previous_event.client_monotonic_ms
            if delta >= 0:
                interval_ms = int(delta)
        timed_steps.append(ProcessStep(
            **{
                **step.__dict__,
                "time_source": (
                    "synchronized_estimate"
                    if source_event.estimated_occurred_at_utc
                    else "server_received_at"
                ),
                "interval_ms": interval_ms,
            }
        ))
        previous_event = source_event
    revision_count = None
    if question_type in {"single_choice", "true_false"}:
        revision_count = max(0, len(path) - 1)
    return AttemptProcessProjection(
        generated_at=generated_at,
        question_type=question_type,
        completeness=ProcessCompleteness.COMPLETE,
        completeness_label="完整链",
        steps=tuple(timed_steps),
        first_value=_state_display(question_type, path[0][2]),
        final_value=final_value,
        revision_count=revision_count,
    )
def _project_choice(
    *, generated_at, attempt_id, question_type, events, final_answer, submitted_at
):
    response_events = tuple(
        event
        for event in events
        if event.event_type == "response_state_changed"
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
        event
        for event in events
        if event.event_type == "response_state_changed"
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
    final_snapshot = snapshot_by_id.get(str(final_snapshot_id))
    if final_snapshot is None or final_snapshot.attempt_id != attempt_id:
        return _incomplete(generated_at, question_type, final_answer)
    final_state = final_snapshot.state_json
    if (
        not isinstance(final_state, dict)
        or final_state.get("question_type") != question_type
        or display_text(final_state.get("value")) != display_text(final_answer)
    ):
        return _incomplete(generated_at, question_type, final_answer)
    values = []
    steps = []
    last_ref = None
    uses_snapshot_refs = None
    for event in response_events:
        payload = event.payload
        if payload.get("question_type") != question_type:
            return _incomplete(generated_at, question_type, final_answer)
        ref = payload.get("response_state_after")
        is_snapshot_ref = isinstance(ref, dict)
        if uses_snapshot_refs is None:
            uses_snapshot_refs = is_snapshot_ref
        elif uses_snapshot_refs != is_snapshot_ref:
            return _incomplete(generated_at, question_type, final_answer)
        if is_snapshot_ref:
            value = _resolve_state_ref(
                ref=ref,
                snapshot_by_id=snapshot_by_id,
                attempt_id=attempt_id,
                question_type=question_type,
            )
            if value is None:
                return _incomplete(generated_at, question_type, final_answer)
            last_ref = ref
        else:
            if ref is not None and not isinstance(ref, str):
                return _incomplete(generated_at, question_type, final_answer)
            value = "" if ref is None else ref
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
    if uses_snapshot_refs and str(last_ref.get("snapshot_id")) != str(final_snapshot_id):
        return _incomplete(generated_at, question_type, final_answer)
    if not values or values[-1] != final_value:
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
    ordered = order_process_records(
        observations, fallback_time_field="observed_at"
    )
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
    final_response_event_id=None,
    formal_capture_boundary=None,
    submission=None,
):
    """Project one attempt into a privacy-bounded teacher-facing process."""
    events = order_process_records(
        events, fallback_time_field="server_ingest_sequence_no"
    )
    if any(event.event_type == "response_transition" for event in events):
        return _capture_v2_projection(
            generated_at=generated_at,
            attempt_id=attempt_id,
            question_type=question_type,
            events=events,
            snapshots=snapshots,
            final_answer=final_answer,
            final_snapshot_id=final_snapshot_id,
            submitted_at=submitted_at,
            final_response_event_id=final_response_event_id,
            formal_capture_boundary=formal_capture_boundary,
            submission=submission,
        )
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
