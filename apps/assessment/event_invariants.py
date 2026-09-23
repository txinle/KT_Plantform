from decimal import Decimal, InvalidOperation

from .models import (
    AssessmentEvent,
    ConfidenceAssignment,
    SimulationSnapshot,
    StudentQuestionAssignment,
    SubmissionEvent,
)


class EventInvariantError(ValueError):
    pass


class ResponseStateChainError(EventInvariantError):
    """Raised when response_state_before/after observations are not a chain."""


def _state_identity(value):
    if isinstance(value, dict):
        # Text refs identify logical state by hash, deliberately ignoring UUID.
        if value.get("state_hash"):
            return ("hash", value["state_hash"])
        if value.get("content_hash"):
            return ("content", value["content_hash"])
    return ("value", value)


def validate_response_state_chain(events):
    """Validate contiguous response state observations in local event order.

    ``events`` may be payload dictionaries or AssessmentEvent-like objects.
    The first state's ``before`` must be null; each subsequent ``before`` must
    equal the preceding ``after``. Snapshot UUIDs are ignored for text refs.
    """
    previous_after = None
    for index, event in enumerate(events):
        payload = event if isinstance(event, dict) else event.payload
        if "response_state_after" not in payload:
            continue
        before = payload.get("response_state_before")
        if index == 0 and before is not None:
            raise ResponseStateChainError("First response state must have null before state.")
        if index > 0 and _state_identity(before) != _state_identity(previous_after):
            raise ResponseStateChainError(
                f"Response state chain break at observation {index}."
            )
        previous_after = payload.get("response_state_after")



TELEMETRY_EVENTS = frozenset(
    {
        "document_visible",
        "document_hidden",
        "window_focused",
        "window_blurred",
        "pageshow",
        "pagehide",
        "network_online",
        "network_offline",
        "activity_heartbeat",
    }
)

SESSION_ONLY_EVENTS = frozenset(
    {
        "session_started",
        "session_completed",
        "session_expired",
        "session_reopened",
        "session_invalidated",
        "legacy_session_start_observed",
        "legacy_session_end_observed",
    }
)

ATTEMPT_EVENTS = frozenset(
    {
        "research_tool",
        "research_tool_observed",
        "research_tool_requested",
        "confidence_observed",
        "feedback_exposure",
        "response_transition",
        "input_operation",
        "response_branch_resolved",
        "response_pointer_conflict_observed",
        "item_entered",
        "item_rendered",
        "item_left",
        "response_input_started",
        "text_edit_observed",
        "response_state_changed",
        "response_commit_requested",
        "response_committed",
        "response_commit_rejected",
        "attempt_closed",
        "attempt_reopened",
        "feedback_shown",
        "explanation_shown",
        "hint_opened",
        "hint_closed",
        "resource_opened",
        "resource_closed",
        "confidence_prompt_shown",
        "confidence_committed",
        "confidence_skipped",
        "simulation_parameter_changed",
        "simulation_run_started",
        "simulation_run_completed",
        "simulation_error_returned",
        "legacy_visit_observed",
        "legacy_draft_observed",
        "legacy_submission_observed",
    }
)

CONFIDENCE_EVENTS = frozenset(
    {"confidence_prompt_shown", "confidence_committed", "confidence_skipped"}
)

SIMULATION_EVENTS = frozenset(
    {
        "simulation_parameter_changed",
        "simulation_run_started",
        "simulation_run_completed",
        "simulation_error_returned",
    }
)


def interval_quality(previous, current):
    """Classify an interval without comparing monotonic clocks across tabs."""
    if previous is None or current is None:
        return "unknown"
    if previous.client_instance_id and previous.client_instance_id == current.client_instance_id:
        if previous.client_time_origin == current.client_time_origin:
            return "exact_monotonic"
    if getattr(previous, "estimated_occurred_at_utc", None) and getattr(current, "estimated_occurred_at_utc", None):
        return "synchronized_estimate"
    return "degraded"


classify_interval_quality = interval_quality


def _require_attempt_links(
    *, event_type, session, assignment, attempt, question_snapshot, item_instance
):
    if event_type not in ATTEMPT_EVENTS:
        return
    if any(
        value is None
        for value in (assignment, attempt, question_snapshot, item_instance)
    ):
        raise EventInvariantError(
            f"{event_type} requires attempt, assignment, question snapshot and item instance."
        )
    if attempt.session_id != session.pk or attempt.assignment_id != assignment.pk:
        raise EventInvariantError("Event attempt links do not form one session assignment.")


def _validate_payload_links(
    *, event_type, payload, session, attempt, question_snapshot
):
    if event_type == "item_entered" and payload["from_assignment_id"] is not None:
        if not StudentQuestionAssignment.objects.filter(
            pk=payload["from_assignment_id"],
            session_id=session.pk,
        ).exists():
            raise EventInvariantError(
                "item_entered.from_assignment_id is outside the event session."
            )
    if event_type == "item_left" and payload.get("to_assignment_id") is not None:
        if not StudentQuestionAssignment.objects.filter(
            pk=payload["to_assignment_id"],
            session_id=session.pk,
        ).exists():
            raise EventInvariantError(
                "item_left.to_assignment_id is outside the event session."
            )
    if event_type == "activity_heartbeat" and (
        attempt is None or payload["attempt_id"] != attempt.pk
    ):
        raise EventInvariantError(
            "activity_heartbeat.attempt_id does not match the event attempt."
        )
    if event_type == "response_state_changed":
        question_type = payload.get("question_type")
        if question_snapshot is None or question_type != question_snapshot.question_type:
            raise EventInvariantError(
                "response_state_changed question type differs from the assigned item."
            )
        before = payload.get("response_state_before")
        after = payload.get("response_state_after")
        if question_type == "single_choice":
            option_keys = {
                str(option.get("key"))
                for option in question_snapshot.options
                if isinstance(option, dict) and option.get("key") is not None
            }
            if after not in option_keys or (before is not None and before not in option_keys):
                raise EventInvariantError("Choice response contains an unknown option.")
        elif question_type == "multiple_choice":
            option_keys = {
                str(option.get("key"))
                for option in question_snapshot.options
                if isinstance(option, dict) and option.get("key") is not None
            }
            for state in (before, after):
                if state is None:
                    continue
                if (
                    not isinstance(state, list)
                    or state != sorted(set(state))
                    or not set(state).issubset(option_keys)
                ):
                    raise EventInvariantError(
                        "Multiple-choice response must be an ordered unique option set."
                    )
    if event_type in CONFIDENCE_EVENTS:
        assignment = ConfidenceAssignment.objects.filter(attempt=attempt).first()
        if assignment is None or str(assignment.pk) != payload["confidence_assignment_id"]:
            raise EventInvariantError(
                "Confidence event does not match the attempt assignment."
            )
        if event_type == "confidence_prompt_shown" and assignment.response_status not in {
            ConfidenceAssignment.ResponseStatus.PROMPT_SHOWN,
            ConfidenceAssignment.ResponseStatus.COMMITTED,
            ConfidenceAssignment.ResponseStatus.SKIPPED,
        }:
            raise EventInvariantError(
                "confidence_prompt_shown requires a displayed prompt projection."
            )
        if event_type == "confidence_committed" and (
            assignment.response_status != ConfidenceAssignment.ResponseStatus.COMMITTED
            or assignment.probability != payload["probability"]
        ):
            raise EventInvariantError(
                "confidence_committed differs from the committed projection."
            )
        if (
            event_type == "confidence_skipped"
            and assignment.response_status != ConfidenceAssignment.ResponseStatus.SKIPPED
        ):
            raise EventInvariantError(
                "confidence_skipped requires a skipped projection."
            )
    if event_type in SIMULATION_EVENTS:
        if not SimulationSnapshot.objects.filter(
            pk=payload["simulation_snapshot_id"],
            attempt=attempt,
        ).exists():
            raise EventInvariantError(
                "Simulation event snapshot does not match the event attempt."
            )


def _validate_submission_invariants(*, event_type, actor_source, payload, attempt):
    if event_type == "response_pointer_conflict_observed" and actor_source != "server":
        raise EventInvariantError(
            "response_pointer_conflict_observed is an authoritative server event."
        )
    if event_type == "response_committed":
        if actor_source != "server":
            raise EventInvariantError(
                "response_committed is an authoritative server event."
            )
        if AssessmentEvent.objects.filter(
            attempt=attempt,
            event_type="response_committed",
        ).exists():
            raise EventInvariantError(
                "An attempt can have only one response_committed event."
            )
        submission = SubmissionEvent.objects.filter(
            attempt=attempt,
            submission_token=payload["submission_token"],
        ).first()
        if submission is None:
            raise EventInvariantError(
                "response_committed requires the authoritative SubmissionEvent."
            )
        if "submission_id" in payload and payload["submission_id"] != submission.pk:
            raise EventInvariantError(
                "response_committed submission reference differs from the authoritative submission."
            )
        if "final_response_snapshot_id" in payload and (
            str(payload["final_response_snapshot_id"] or "")
            != str(submission.final_response_snapshot_id or "")
        ):
            raise EventInvariantError(
                "response_committed final snapshot differs from the authoritative submission."
            )
        if "grading_status" in payload and payload["grading_status"] != submission.grading_status:
            raise EventInvariantError(
                "response_committed grading status differs from the authoritative submission."
            )
        # Grade values are optional while grading is pending/regrade.  When
        # present, they remain a strict projection of the canonical submission.
        if "score" in payload:
            try:
                payload_score = Decimal(str(payload["score"]))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise EventInvariantError("response_committed score is invalid.") from error
            if payload_score != submission.score:
                raise EventInvariantError(
                    "response_committed score differs from the authoritative submission."
                )
        if "is_correct" in payload and payload["is_correct"] != submission.is_correct:
            raise EventInvariantError(
                "response_committed correctness differs from the authoritative submission."
            )
    if event_type in {"feedback_shown", "explanation_shown", "feedback_exposure"}:
        if not AssessmentEvent.objects.filter(
            attempt=attempt,
            event_type="response_committed",
        ).exists():
            raise EventInvariantError(
                f"{event_type} requires a prior response_committed event."
            )
        confidence = ConfidenceAssignment.objects.filter(attempt=attempt).first()
        if (
            confidence is not None
            and confidence.condition == ConfidenceAssignment.Condition.PROMPT
            and confidence.response_status
            not in {
                ConfidenceAssignment.ResponseStatus.COMMITTED,
                ConfidenceAssignment.ResponseStatus.SKIPPED,
            }
        ):
            raise EventInvariantError(
                f"{event_type} requires the assigned Confidence step to resolve."
            )
    if event_type == "attempt_closed" and payload["close_reason"] == "submitted":
        if actor_source != "server":
            raise EventInvariantError(
                "A submitted attempt_closed event is authoritative server output."
            )
        if not SubmissionEvent.objects.filter(attempt=attempt).exists():
            raise EventInvariantError(
                "A submitted attempt_closed event requires SubmissionEvent."
            )
        if AssessmentEvent.objects.filter(
            attempt=attempt,
            event_type="response_committed",
        ).count() != 1:
            raise EventInvariantError(
                "A submitted attempt must have exactly one response_committed event."
            )


def validate_event_invariants(
    *,
    event_type,
    event_domain,
    actor_source,
    capture_mode,
    payload,
    session,
    assignment,
    attempt,
    question_snapshot,
    item_instance,
):
    expected_domain = "telemetry" if event_type in TELEMETRY_EVENTS else "assessment"
    if event_domain != expected_domain:
        raise EventInvariantError(
            f"{event_type} belongs to the {expected_domain} event domain."
        )
    is_legacy_event = event_type.startswith("legacy_")
    if (capture_mode == "legacy_projection") != is_legacy_event:
        raise EventInvariantError(
            "Legacy event types and legacy_projection capture mode must match."
        )
    if question_snapshot is not None and question_snapshot.version_id != session.version_id:
        raise EventInvariantError(
            "Question snapshot does not belong to the event session version."
        )
    _require_attempt_links(
        event_type=event_type,
        session=session,
        assignment=assignment,
        attempt=attempt,
        question_snapshot=question_snapshot,
        item_instance=item_instance,
    )
    if event_type in SESSION_ONLY_EVENTS and any(
        value is not None
        for value in (assignment, attempt, question_snapshot, item_instance)
    ):
        raise EventInvariantError(f"{event_type} is a session-level event.")
    _validate_payload_links(
        event_type=event_type,
        payload=payload,
        session=session,
        attempt=attempt,
        question_snapshot=question_snapshot,
    )
    _validate_submission_invariants(
        event_type=event_type,
        actor_source=actor_source,
        payload=payload,
        attempt=attempt,
    )
