"""Small, privacy-preserving helpers for stable text-input observations."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q

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


def _check_replay(existing, incoming):
    # Legacy records may lack a source event. Preserve the original link and
    # compare exactly the stored stable-input material, including null boundaries.
    fields = ('client_monotonic_ms', 'batch_start_client_monotonic_ms',
              'batch_end_client_monotonic_ms', 'edit_operation', 'content_hash_before',
              'content_hash_after', 'content_length_before', 'content_length_after',
              'input_method', 'is_ime')
    if any(getattr(existing, name) != getattr(incoming, name) for name in fields):
        raise ValueError("Input observation sequence conflicts with existing observation.")
    return existing


def _append_input_observations(rows, *, bulk):
    """Append after parent events flush, inside the event batch's atomic retry."""
    if not rows:
        return
    if not bulk:
        for row in rows:
            _append_one_input_observation(row)
        return

    sequences = {}
    for row in rows:
        sequences.setdefault((row.attempt_id, row.client_instance_id), []).append(row.client_sequence_no)
    keys = Q(pk__in=[])
    for (attempt_id, client_id), numbers in sequences.items():
        keys |= Q(attempt_id=attempt_id, client_instance_id=client_id, client_sequence_no__in=numbers)
    existing = {(row.attempt_id, row.client_instance_id, row.client_sequence_no): row
                for row in ResponseInputObservation.objects.filter(keys)}
    new = []
    for row in rows:
        key = (row.attempt_id, row.client_instance_id, row.client_sequence_no)
        if key in existing:
            _check_replay(existing[key], row)
        else:
            new.append(row)
            existing[key] = row
    if new:
        with _response_observation_append_scope():
            ResponseInputObservation.objects.bulk_create(new)


@transaction.atomic
def _append_one_input_observation(row):
    by_key = ResponseInputObservation.objects.filter(
        attempt_id=row.attempt_id, client_instance_id=row.client_instance_id,
        client_sequence_no=row.client_sequence_no)
    existing = by_key.first()
    if existing is not None:
        return _check_replay(existing, row)
    try:
        with transaction.atomic(), _response_observation_append_scope():
            row.save(force_insert=True)
    except IntegrityError:
        existing = by_key.first()
        if existing is None:
            raise
        return _check_replay(existing, row)
    return row


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
    return _append_one_input_observation(ResponseInputObservation(
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
    ))
