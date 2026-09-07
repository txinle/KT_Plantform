import hashlib
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from .event_services import append_assessment_event
from .feedback_services import frozen_feedback_payload
from .models import Attempt, ConfidenceAssignment


class ConfidenceFlowError(ValueError):
    pass


def _configuration(attempt):
    return attempt.session.version.configuration or {}


def _assignment_unit_key(attempt, configuration):
    unit = configuration.get("confidence_sampling_unit", "attempt")
    if unit == ConfidenceAssignment.AssignmentUnit.STUDENT:
        return f"student:{attempt.session.student_id}"
    if unit == ConfidenceAssignment.AssignmentUnit.SESSION:
        return f"session:{attempt.session_id}"
    if unit == ConfidenceAssignment.AssignmentUnit.ATTEMPT:
        return f"attempt:{attempt.pk}"
    raise ConfidenceFlowError("Unknown Confidence sampling unit.")


def confidence_assignment_score(attempt, configuration=None):
    configuration = configuration or _configuration(attempt)
    unit = configuration.get("confidence_sampling_unit", "attempt")
    assignment_version = configuration.get("assignment_version", "v1")
    randomization_seed = configuration.get("randomization_seed", "")
    material = "|".join(
        (
            str(randomization_seed),
            str(assignment_version),
            str(unit),
            _assignment_unit_key(attempt, configuration),
        )
    )
    numerator = int.from_bytes(
        hashlib.sha256(material.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=False,
    )
    return Decimal(numerator) / Decimal(2**64)


def _condition_for_attempt(attempt, configuration):
    mode = configuration.get("confidence_mode", "off")
    if mode == "off":
        return ConfidenceAssignment.Condition.NOT_ASSIGNED
    if mode == "all":
        return ConfidenceAssignment.Condition.PROMPT
    if mode != "sampled":
        raise ConfidenceFlowError("Unknown Confidence collection mode.")
    try:
        probability = Decimal(str(configuration.get("confidence_probability")))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ConfidenceFlowError("Confidence sampling probability is invalid.") from error
    if not probability.is_finite() or probability < 0 or probability > 1:
        raise ConfidenceFlowError("Confidence sampling probability is invalid.")
    if confidence_assignment_score(attempt, configuration) < probability:
        return ConfidenceAssignment.Condition.PROMPT
    return ConfidenceAssignment.Condition.CONTROL


@transaction.atomic
def ensure_confidence_assignment(attempt):
    attempt = Attempt.objects.select_related(
        "session__version",
    ).get(pk=attempt.pk)
    configuration = _configuration(attempt)
    condition = _condition_for_attempt(attempt, configuration)
    response_status = (
        ConfidenceAssignment.ResponseStatus.NOT_ASSIGNED
        if condition == ConfidenceAssignment.Condition.NOT_ASSIGNED
        else ConfidenceAssignment.ResponseStatus.ASSIGNED
    )
    defaults = {
        "condition": condition,
        "assignment_unit": configuration.get(
            "confidence_sampling_unit",
            ConfidenceAssignment.AssignmentUnit.ATTEMPT,
        ),
        "assignment_version": configuration.get("assignment_version", "v1"),
        "response_status": response_status,
    }
    try:
        assignment, created = ConfidenceAssignment.objects.get_or_create(
            attempt=attempt,
            defaults=defaults,
        )
    except IntegrityError:
        assignment = ConfidenceAssignment.objects.get(attempt=attempt)
        created = False
    if not created and any(
        getattr(assignment, field) != value for field, value in defaults.items()
    ):
        raise ConfidenceFlowError(
            "Stored Confidence assignment differs from the frozen experiment configuration."
        )
    return assignment


@transaction.atomic
def materialize_confidence_assignments(session):
    attempts = list(
        Attempt.objects.filter(session=session)
        .select_related("session__version")
        .order_by("pk")
    )
    return [ensure_confidence_assignment(attempt) for attempt in attempts]


def confidence_prompt_contract(assignment):
    configuration = _configuration(assignment.attempt)
    return {
        "confidence_assignment_id": str(assignment.pk),
        "scale_version": configuration.get("confidence_scale_version", "v1"),
        "prompt_version": configuration.get("confidence_prompt_version", "v1"),
        "minimum": 0,
        "maximum": 100,
        "step": 10,
    }


def _append_confidence_event(assignment, *, event_type, payload):
    attempt = assignment.attempt
    return append_assessment_event(
        event_type=event_type,
        event_version=1,
        event_domain="assessment",
        actor_source="server",
        session=attempt.session,
        assignment=attempt.assignment,
        attempt=attempt,
        question_snapshot=attempt.assignment.snapshot,
        item_instance=attempt.assignment.item_instance,
        payload=payload,
        capture_mode="native",
        app_version="server",
    )


def _locked_assignment(*, attempt_id, student):
    assignment = (
        ConfidenceAssignment.objects.select_for_update()
        .select_related(
            "attempt__session__version__experiment",
            "attempt__assignment__snapshot",
            "attempt__assignment__item_instance",
        )
        .filter(
            attempt_id=attempt_id,
            attempt__session__student=student,
        )
        .first()
    )
    if assignment is None:
        raise ConfidenceFlowError("Confidence assignment is unavailable.")
    if assignment.condition != ConfidenceAssignment.Condition.PROMPT:
        raise ConfidenceFlowError("This attempt was not assigned a Confidence prompt.")
    return assignment


@transaction.atomic
def mark_confidence_prompt_shown(*, attempt_id, student):
    assignment = _locked_assignment(attempt_id=attempt_id, student=student)
    if assignment.response_status in {
        ConfidenceAssignment.ResponseStatus.PROMPT_SHOWN,
        ConfidenceAssignment.ResponseStatus.COMMITTED,
        ConfidenceAssignment.ResponseStatus.SKIPPED,
    }:
        return assignment
    if assignment.response_status != ConfidenceAssignment.ResponseStatus.ASSIGNED:
        raise ConfidenceFlowError("Confidence prompt cannot be shown in this state.")
    assignment.response_status = ConfidenceAssignment.ResponseStatus.PROMPT_SHOWN
    assignment.prompt_shown_at = timezone.now()
    assignment.save(update_fields=["response_status", "prompt_shown_at"])
    configuration = _configuration(assignment.attempt)
    _append_confidence_event(
        assignment,
        event_type="confidence_prompt_shown",
        payload={
            "confidence_assignment_id": str(assignment.pk),
            "scale_version": configuration.get("confidence_scale_version", "v1"),
            "prompt_version": configuration.get("confidence_prompt_version", "v1"),
        },
    )
    return assignment


@transaction.atomic
def commit_confidence(*, attempt_id, student, probability):
    if (
        isinstance(probability, bool)
        or not isinstance(probability, int)
        or probability < 0
        or probability > 100
        or probability % 10
    ):
        raise ConfidenceFlowError("Confidence probability must use 10 percent steps.")
    assignment = _locked_assignment(attempt_id=attempt_id, student=student)
    if assignment.response_status == ConfidenceAssignment.ResponseStatus.COMMITTED:
        if assignment.probability == probability:
            return assignment
        raise ConfidenceFlowError("Committed Confidence cannot be changed.")
    if assignment.response_status != ConfidenceAssignment.ResponseStatus.PROMPT_SHOWN:
        raise ConfidenceFlowError("Confidence cannot be committed in this state.")
    assignment.response_status = ConfidenceAssignment.ResponseStatus.COMMITTED
    assignment.probability = probability
    assignment.committed_at = timezone.now()
    assignment.save(
        update_fields=["response_status", "probability", "committed_at"]
    )
    _append_confidence_event(
        assignment,
        event_type="confidence_committed",
        payload={
            "confidence_assignment_id": str(assignment.pk),
            "probability": probability,
        },
    )
    return assignment


@transaction.atomic
def skip_confidence(*, attempt_id, student):
    assignment = _locked_assignment(attempt_id=attempt_id, student=student)
    if assignment.response_status == ConfidenceAssignment.ResponseStatus.SKIPPED:
        return assignment
    if assignment.response_status != ConfidenceAssignment.ResponseStatus.PROMPT_SHOWN:
        raise ConfidenceFlowError("Confidence cannot be skipped in this state.")
    assignment.response_status = ConfidenceAssignment.ResponseStatus.SKIPPED
    assignment.save(update_fields=["response_status"])
    _append_confidence_event(
        assignment,
        event_type="confidence_skipped",
        payload={
            "confidence_assignment_id": str(assignment.pk),
            "reason_code": "student_skipped",
        },
    )
    return assignment
