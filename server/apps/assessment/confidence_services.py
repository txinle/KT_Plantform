import hashlib
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.experiments.confidence_scales import confidence_scale

from .event_services import append_assessment_event
from .feedback_services import frozen_feedback_payload
from .models import Attempt, ConfidenceAssignment


class ConfidenceFlowError(ValueError):
    pass


def _configuration(attempt):
    return attempt.session.version.configuration or {}


def confidence_scale_contract(attempt):
    version = _configuration(attempt).get("confidence_scale_version", "v1")
    try:
        return {"scale_version": version, **confidence_scale(version)}
    except ValueError as error:
        raise ConfidenceFlowError("未知的信心量表版本。") from error


def validate_confidence_probability(attempt, probability):
    scale = confidence_scale_contract(attempt)
    minimum = scale["minimum"]
    maximum = scale["maximum"]
    step = scale["step"]
    if (
        isinstance(probability, bool)
        or not isinstance(probability, int)
        or probability < minimum
        or probability > maximum
        or (probability - minimum) % step
    ):
        raise ConfidenceFlowError(
            f"请选择 {minimum} 到 {maximum} 之间、以 {step} 为间隔的整数信心值。"
        )
    return probability


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
        getattr(assignment, field) != value
        for field, value in defaults.items() if field != "response_status"
    ):
        raise ConfidenceFlowError(
            "Stored Confidence assignment differs from the frozen experiment configuration."
        )
    return assignment


def validate_submission_confidence(attempt, response):
    """Validate the final decision without recording a separate submission."""
    assignment = ensure_confidence_assignment(attempt)
    if assignment.condition != ConfidenceAssignment.Condition.PROMPT:
        if response is not None:
            raise ConfidenceFlowError("本题未分配信心评价。")
        return None
    if not isinstance(response, dict) or response.get('status') not in {'committed', 'skipped'}:
        raise ConfidenceFlowError("请先评价作答信心，或明确选择跳过，再确认提交。")
    if set(response) - {'status', 'probability', 'answer_event_id'}:
        raise ConfidenceFlowError("信心评价内容无效。")
    if assignment.response_status != ConfidenceAssignment.ResponseStatus.PROMPT_SHOWN:
        raise ConfidenceFlowError("请先打开信心评价，再确认提交。")
    if attempt.active_response_event_id is not None and str(response.get('answer_event_id')) != str(attempt.active_response_event_id):
        raise ConfidenceFlowError("答案已发生变化，请重新评价或跳过。")
    probability = response.get('probability')
    if response['status'] == 'skipped':
        if probability is not None:
            raise ConfidenceFlowError("跳过评价不能同时填写信心值。")
    else:
        validate_confidence_probability(attempt, probability)
    return assignment


@transaction.atomic
def materialize_confidence_assignments(session, *, new_attempts=None):
    # Session creation already holds the persisted attempts and pinned version.
    attempts = list(new_attempts) if new_attempts is not None else list(
        Attempt.objects.filter(session=session)
        .select_related("session__version")
        .order_by("pk")
    )
    existing = {
        assignment.attempt_id: assignment
        for assignment in ConfidenceAssignment.objects.filter(
            attempt_id__in=[attempt.pk for attempt in attempts]
        )
    }
    new_rows = []
    for attempt in attempts:
        configuration = _configuration(attempt)
        condition = _condition_for_attempt(attempt, configuration)
        response_status = (
            ConfidenceAssignment.ResponseStatus.NOT_ASSIGNED
            if condition == ConfidenceAssignment.Condition.NOT_ASSIGNED
            else ConfidenceAssignment.ResponseStatus.ASSIGNED
        )
        expected = {
            "condition": condition,
            "assignment_unit": configuration.get(
                "confidence_sampling_unit",
                ConfidenceAssignment.AssignmentUnit.ATTEMPT,
            ),
            "assignment_version": configuration.get("assignment_version", "v1"),
            "response_status": response_status,
        }
        stored = existing.get(attempt.pk)
        if stored is not None:
            if any(
                getattr(stored, field) != value
                for field, value in expected.items()
                if field != "response_status"
            ):
                raise ConfidenceFlowError(
                    "Stored Confidence assignment differs from the frozen experiment configuration."
                )
            continue
        new_rows.append(ConfidenceAssignment(attempt=attempt, **expected))
    if new_rows:
        ConfidenceAssignment.objects.bulk_create(
            new_rows,
            ignore_conflicts=True,
            batch_size=500,
        )
    by_attempt = {
        assignment.attempt_id: assignment
        for assignment in ConfidenceAssignment.objects.filter(
            attempt_id__in=[attempt.pk for attempt in attempts]
        )
    }
    return [by_attempt[attempt.pk] for attempt in attempts]


def confidence_prompt_contract(assignment):
    configuration = _configuration(assignment.attempt)
    return {
        "confidence_assignment_id": str(assignment.pk),
        **confidence_scale_contract(assignment.attempt),
        "prompt_version": configuration.get("confidence_prompt_version", "v1"),
    }


def _append_confidence_event(assignment, *, event_type, payload):
    attempt = assignment.attempt
    scale_version = _configuration(attempt).get("confidence_scale_version", "v1")
    event_version = (
        2
        if (
            event_type == "confidence_skipped"
            and payload.get("reason_code") == "deadline_expired"
        )
        else 2
        if event_type == "confidence_committed" and scale_version == "v2"
        else 1
    )
    return append_assessment_event(
        event_type=event_type,
        event_version=event_version,
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


def _locked_assignment(*, attempt_id, student, request_received_at=None):
    from .models import ExperimentSession

    link = Attempt.objects.filter(pk=attempt_id, session__student=student).values_list('session_id', 'session__class_task_id').first()
    if link is None:
        raise ConfidenceFlowError('Confidence assignment is unavailable.')
    session_id, _task_id = link
    session = ExperimentSession.objects.select_for_update().get(pk=session_id)
    Attempt.objects.select_for_update().get(pk=attempt_id)
    assignment = (
        ConfidenceAssignment.objects.select_for_update(of=('self',))
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
    from .services import _validate_session_time

    try:
        time_error = _validate_session_time(
            session,
            assignment.attempt,
            request_received_at=request_received_at or timezone.now(),
        )
    except ValidationError as exc:
        raise ConfidenceFlowError("当前作答已不可继续。") from exc
    if time_error:
        raise ConfidenceFlowError(time_error)
    if assignment.condition != ConfidenceAssignment.Condition.PROMPT:
        raise ConfidenceFlowError("This attempt was not assigned a Confidence prompt.")
    return assignment


@transaction.atomic
def mark_confidence_prompt_shown(*, attempt_id, student, request_received_at=None):
    assignment = _locked_assignment(
        attempt_id=attempt_id,
        student=student,
        request_received_at=request_received_at,
    )
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
def commit_confidence(*, attempt_id, student, probability, request_received_at=None):
    assignment = _locked_assignment(
        attempt_id=attempt_id,
        student=student,
        request_received_at=request_received_at,
    )
    validate_confidence_probability(assignment.attempt, probability)
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
def skip_confidence(*, attempt_id, student, request_received_at=None):
    assignment = _locked_assignment(
        attempt_id=attempt_id,
        student=student,
        request_received_at=request_received_at,
    )
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
