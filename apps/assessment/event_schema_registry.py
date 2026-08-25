from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_ROOT = Path(__file__).with_name("event_schemas")

REGISTERED_EVENT_TYPES = (
    "session_started",
    "session_completed",
    "session_expired",
    "session_invalidated",
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
    "document_visible",
    "document_hidden",
    "window_focused",
    "window_blurred",
    "pageshow",
    "pagehide",
    "network_online",
    "network_offline",
    "activity_heartbeat",
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
    "legacy_session_start_observed",
    "legacy_session_end_observed",
)


class EventSchemaNotFound(LookupError):
    pass


class EventPayloadValidationError(ValueError):
    pass


def _schema_path(event_type: str, event_version: int) -> Path:
    if event_type not in REGISTERED_EVENT_TYPES or event_version < 1:
        raise EventSchemaNotFound(f"Unregistered event schema: {event_type}:v{event_version}")
    path = SCHEMA_ROOT / event_type / f"v{event_version}.json"
    if not path.is_file():
        raise EventSchemaNotFound(f"Unregistered event schema: {event_type}:v{event_version}")
    return path


@lru_cache(maxsize=None)
def _load_event_schema(event_type: str, event_version: int) -> dict:
    path = _schema_path(event_type, event_version)
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def get_event_schema(event_type: str, event_version: int) -> dict:
    return deepcopy(_load_event_schema(event_type, event_version))


def validate_event_payload(event_type: str, event_version: int, payload: dict) -> None:
    schema = _load_event_schema(event_type, event_version)
    errors = tuple(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload)
    )
    if errors:
        raise EventPayloadValidationError(
            f"Payload failed validation for {event_type}:v{event_version}"
        )


def event_schema_sha256(event_type: str, event_version: int) -> str:
    return hashlib.sha256(_schema_path(event_type, event_version).read_bytes()).hexdigest()


def registered_event_schemas() -> tuple[tuple[str, int], ...]:
    registrations = tuple((event_type, 1) for event_type in REGISTERED_EVENT_TYPES)
    for event_type, event_version in registrations:
        _load_event_schema(event_type, event_version)
    return registrations
