from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_ROOT = Path(__file__).with_name("event_schemas")

REGISTERED_EVENT_TYPES = (
    "research_tool",
    "research_tool_observed",
    "research_tool_requested",
    "confidence_observed",
    "feedback_exposure",
    "response_transition",
    "input_operation",
    "response_branch_resolved",
    "response_pointer_conflict_observed",
    "session_started",
    "session_completed",
    "session_expired",
    "session_invalidated",
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
        first_error = errors[0]
        json_path = "$"
        for segment in first_error.absolute_path:
            if isinstance(segment, int):
                json_path += f"[{segment}]"
            elif str(segment).isidentifier():
                json_path += f".{segment}"
            else:
                json_path += f"[{json.dumps(str(segment))}]"
        raise EventPayloadValidationError(
            f"Payload failed validation for {event_type}:v{event_version} at {json_path}"
        )


def event_schema_sha256(event_type: str, event_version: int) -> str:
    canonical = json.dumps(
        _load_event_schema(event_type, event_version),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def registered_event_schemas() -> tuple[tuple[str, int], ...]:
    # Compatibility API: this registry historically enumerates the frozen v1
    # baseline. New versions are addressed explicitly through get_event_schema.
    capture_v2_types = {'response_transition', 'input_operation', 'response_branch_resolved',
                        'research_tool', 'research_tool_observed', 'research_tool_requested', 'confidence_observed',
                        'feedback_exposure', 'response_pointer_conflict_observed'}
    registrations = tuple((event_type, 1) for event_type in REGISTERED_EVENT_TYPES if event_type not in capture_v2_types)
    for event_type, event_version in registrations:
        _load_event_schema(event_type, event_version)
    return registrations
