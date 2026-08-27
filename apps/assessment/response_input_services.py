"""Small, privacy-preserving helpers for stable text-input observations."""

from __future__ import annotations


def replay_stable_input(observations):
    """Replay observations while excluding IME composition intermediates.

    Observations are metadata envelopes; only the final non-composing value is
    returned. This helper never emits a composing buffer as a response state.
    """
    stable = ""
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("is_composing"):
            continue
        if "text" in observation and isinstance(observation["text"], str):
            stable = observation["text"]
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

