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
        "session_invalidated",
        "legacy_session_start_observed",
        "legacy_session_end_observed",
    }
)

ATTEMPT_EVENTS = frozenset(
    {
        "item_entered",
        "item_rendered",
        "item_left",
        "response_input_started",
        "response_state_changed",
        "response_commit_requested",
        "response_committed",
        "response_commit_rejected",
        "attempt_closed",
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
) | TELEMETRY_EVENTS

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


def _validate_payload_links(*, event_type, payload, session, attempt):
    if event_type == "item_entered" and payload["from_assignment_id"] is not None:
        if not StudentQuestionAssignment.objects.filter(
            pk=payload["from_assignment_id"],
            session_id=session.pk,
        ).exists():
            raise EventInvariantError(
                "item_entered.from_assignment_id is outside the event session."
            )
    if event_type == "activity_heartbeat" and payload["attempt_id"] != attempt.pk:
        raise EventInvariantError(
            "activity_heartbeat.attempt_id does not match the event attempt."
        )
    if event_type in CONFIDENCE_EVENTS:
        assignment = ConfidenceAssignment.objects.filter(attempt=attempt).first()
        if assignment is None or str(assignment.pk) != payload["confidence_assignment_id"]:
            raise EventInvariantError(
                "Confidence event does not match the attempt assignment."
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
        try:
            payload_score = Decimal(str(payload["score"]))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise EventInvariantError("response_committed score is invalid.") from error
        if payload_score != submission.score or payload["is_correct"] != submission.is_correct:
            raise EventInvariantError(
                "response_committed outcome differs from the authoritative submission."
            )
    if event_type in {"feedback_shown", "explanation_shown"}:
        if not AssessmentEvent.objects.filter(
            attempt=attempt,
            event_type="response_committed",
        ).exists():
            raise EventInvariantError(
                f"{event_type} requires a prior response_committed event."
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
    )
    _validate_submission_invariants(
        event_type=event_type,
        actor_source=actor_source,
        payload=payload,
        attempt=attempt,
    )
