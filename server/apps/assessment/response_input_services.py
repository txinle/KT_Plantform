"""Small, privacy-preserving helpers for stable text-input observations."""

from __future__ import annotations

from django.db import IntegrityError, transaction

from .models import ResponseInputObservation, _response_observation_append_scope


def replay_stable_input(observations):
    """Replay the metadata stream while excluding IME composition states.

    The return value is intentionally metadata-only. Text checkpoints must be
    supplied separately through ``ResponseSnapshot``; this function never
    reconstructs or returns raw text from an edit observation.
    """
    stable = []
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("is_composing"):
            continue
        stable.append(
            {
                key: value
                for key, value in observation.items()
                if key != "text"
            }
        )
    return stable


def stable_input_observation(*, is_composing=False, input_method="unknown", text_length=None):
    """Build the intentionally light-weight observation payload.

    Raw text is not accepted; callers may report length and input method only.
    """
    payload = {
        "is_composing": bool(is_composing),
        "input_method": input_method,
    }
    if text_length is not None:
        payload["text_length"] = int(text_length)
    return payload


@transaction.atomic
def record_stable_input_observation(
    *,
    attempt,
    client_instance_id,
    client_sequence_no,
    client_monotonic_ms,
    edit_operation,
    content_hash_before,
    content_hash_after,
    content_length_before,
    content_length_after,
    input_method="unknown",
    batch_start_client_monotonic_ms=None,
    batch_end_client_monotonic_ms=None,
    is_ime=False,
    source_event=None,
):
    """Append one stable-input observation without accepting response text."""
    values = dict(
        attempt=attempt,
        source_event=source_event,
        client_instance_id=client_instance_id,
        client_sequence_no=client_sequence_no,
        client_monotonic_ms=client_monotonic_ms,
        batch_start_client_monotonic_ms=batch_start_client_monotonic_ms,
        batch_end_client_monotonic_ms=batch_end_client_monotonic_ms,
        edit_operation=edit_operation,
        content_hash_before=content_hash_before,
        content_hash_after=content_hash_after,
        content_length_before=content_length_before,
        content_length_after=content_length_after,
        input_method=input_method,
        is_ime=is_ime,
    )
    existing = ResponseInputObservation.objects.filter(
        attempt=attempt,
        client_instance_id=client_instance_id,
        client_sequence_no=client_sequence_no,
    ).first()
    if existing is not None:
        comparable = {
            key: getattr(existing, key)
            for key in values
            if key not in {"attempt", "source_event"}
        }
        incoming = {
            key: value
            for key, value in values.items()
            if key not in {"attempt", "source_event"}
        }
        if comparable != incoming:
            raise ValueError("Input observation sequence conflicts with existing observation.")
        return existing
    try:
        with _response_observation_append_scope():
            return ResponseInputObservation.objects.create(**values)
    except IntegrityError:
        existing = ResponseInputObservation.objects.filter(
            attempt=attempt,
            client_instance_id=client_instance_id,
            client_sequence_no=client_sequence_no,
        ).first()
        if existing is None:
            raise
        return existing
