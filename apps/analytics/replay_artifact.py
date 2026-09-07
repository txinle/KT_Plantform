"""A stdlib-only replay validator embedded in complete export archives."""


STANDALONE_RESPONSE_REPLAY_SCRIPT = r'''#!/usr/bin/env python3
"""Validate a frozen complete-export response replay without Django or a database.

Usage:
    python replay_response_trajectory.py /path/to/complete-export.zip
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile


REQUIRED_TABLES = (
    "assessment_events",
    "attempts",
    "response_snapshots",
    "response_trajectory",
    "response_replay",
    "session_summary",
)


class ValidationError(RuntimeError):
    pass


def _text(value):
    return "" if value is None else str(value)


def _integer(value, label):
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{label} is not an integer") from error


def _json(value, label):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValidationError(f"{label} is not valid JSON") from error


def _state_hash(state):
    if not isinstance(state, dict):
        raise ValidationError("response state is not an object")
    return hashlib.sha256(
        json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _read_csv(archive, path):
    try:
        raw = archive.read(path)
    except KeyError as error:
        raise ValidationError(f"archive is missing {path}") from error
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except UnicodeDecodeError as error:
        raise ValidationError(f"{path} is not UTF-8 CSV") from error
    return raw, rows


def _require(row, field, label):
    value = row.get(field, "")
    if value in (None, ""):
        raise ValidationError(f"{label} is missing {field}")
    return value


def _require_equal(actual, expected, label):
    if actual != expected:
        raise ValidationError(f"{label} does not match frozen source")


def _validate_table_hashes(manifest, raw_tables):
    resource_manifest = manifest.get("resource_manifest")
    if not isinstance(resource_manifest, dict):
        raise ValidationError("manifest is missing resource_manifest")
    expected_hashes = resource_manifest.get("table_sha256")
    if not isinstance(expected_hashes, dict):
        raise ValidationError("manifest is missing table_sha256")
    for table, raw in raw_tables.items():
        expected = expected_hashes.get(table)
        if not isinstance(expected, str):
            raise ValidationError(f"manifest is missing sha256 for {table}")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValidationError(f"{table} sha256 mismatch")


def _chain_for(head_event_id, transitions):
    if not head_event_id:
        return []
    chain = []
    seen = set()
    current = head_event_id
    while current:
        if current in seen:
            raise ValidationError(f"response replay cycle at {current}")
        seen.add(current)
        transition = transitions.get(current)
        if transition is None:
            raise ValidationError(f"response replay references missing transition {current}")
        chain.append(transition)
        current = transition["parent_event_id"]
    chain.reverse()
    return chain


def _validate_replay_chain(serialized, expected_chain, label):
    supplied = _json(serialized, label)
    if not isinstance(supplied, list):
        raise ValidationError(f"{label} is not a list")
    if len(supplied) != len(expected_chain):
        raise ValidationError(f"{label} length does not match trajectory")
    frozen_fields = (
        "event_id", "parent_event_id", "before_snapshot_id", "after_snapshot_id",
        "before_state_hash", "after_state_hash",
    )
    for supplied_event, expected_event in zip(supplied, expected_chain):
        if not isinstance(supplied_event, dict):
            raise ValidationError(f"{label} contains a non-object event")
        for field in frozen_fields:
            if _text(supplied_event.get(field)) != _text(expected_event.get(field)):
                raise ValidationError(f"{label} {field} does not match trajectory")


def _validate_response_state(value, state_hash, label):
    state = _json(value, label)
    if _state_hash(state) != state_hash:
        raise ValidationError(f"{label} hash mismatch")
    return state


def validate(archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        try:
            manifest = _json(archive.read("00_manifest/manifest.json").decode("utf-8"), "manifest")
        except KeyError as error:
            raise ValidationError("archive is missing 00_manifest/manifest.json") from error
        if not isinstance(manifest, dict):
            raise ValidationError("manifest is not an object")

        paths = {
            "assessment_events": "03_raw_events/assessment_events.csv",
            "attempts": "02_core/attempts.csv",
            "response_snapshots": "03_raw_events/response_snapshots.csv",
            "response_trajectory": "06_derived/response_trajectory.csv",
            "response_replay": "06_derived/response_replay.csv",
            "session_summary": "06_derived/session_summary.csv",
        }
        raw_tables = {}
        tables = {}
        for table in REQUIRED_TABLES:
            raw_tables[table], tables[table] = _read_csv(archive, paths[table])
        _validate_table_hashes(manifest, raw_tables)

    attempts = {}
    for row in tables["attempts"]:
        attempt_id = row.get("attempt_id", "")
        if attempt_id:
            if attempt_id in attempts:
                raise ValidationError(f"duplicate attempt {attempt_id}")
            attempts[attempt_id] = row

    snapshots = {}
    for row in tables["response_snapshots"]:
        snapshot_id = _require(row, "snapshot_id", "response snapshot")
        if snapshot_id in snapshots:
            raise ValidationError(f"duplicate response snapshot {snapshot_id}")
        state = _validate_response_state(
            _require(row, "state_json", f"response snapshot {snapshot_id}"),
            _require(row, "state_hash", f"response snapshot {snapshot_id}"),
            f"response snapshot {snapshot_id}",
        )
        snapshots[snapshot_id] = {"row": row, "state": state}

    raw_events = {}
    for row in tables["assessment_events"]:
        event_id = _require(row, "event_id", "assessment event")
        if event_id in raw_events:
            raise ValidationError(f"duplicate assessment event {event_id}")
        raw_events[event_id] = row

    transitions = {}
    for row in tables["response_trajectory"]:
        event_id = _require(row, "event_id", "response trajectory")
        if event_id in transitions:
            raise ValidationError(f"duplicate response transition {event_id}")
        attempt_id = _require(row, "attempt_id", f"response transition {event_id}")
        if attempt_id not in attempts:
            raise ValidationError(f"response transition {event_id} has no exported attempt")
        before_state = _validate_response_state(
            _require(row, "before_state", f"response transition {event_id}"),
            _require(row, "before_state_hash", f"response transition {event_id}"),
            f"response transition {event_id} before state",
        )
        after_state = _validate_response_state(
            _require(row, "after_state", f"response transition {event_id}"),
            _require(row, "after_state_hash", f"response transition {event_id}"),
            f"response transition {event_id} after state",
        )
        for side, state, state_hash in (
            ("before", before_state, row["before_state_hash"]),
            ("after", after_state, row["after_state_hash"]),
        ):
            snapshot_id = _require(row, f"{side}_snapshot_id", f"response transition {event_id}")
            snapshot = snapshots.get(snapshot_id)
            if snapshot is None:
                raise ValidationError(f"response transition {event_id} references missing {side} snapshot")
            if snapshot["row"].get("attempt_id") != attempt_id:
                raise ValidationError(f"response transition {event_id} {side} snapshot has another attempt")
            if snapshot["row"].get("state_hash") != state_hash or snapshot["state"] != state:
                raise ValidationError(f"response transition {event_id} {side} snapshot does not match state")
        raw_event = raw_events.get(event_id)
        if raw_event is None or raw_event.get("event_type") != "response_transition":
            raise ValidationError(f"response transition {event_id} is absent from raw assessment events")
        raw_payload = _json(raw_event.get("payload", ""), f"raw response transition {event_id}")
        for field in (
            "parent_event_id", "before_snapshot_id", "after_snapshot_id",
            "before_state_hash", "after_state_hash",
        ):
            if _text(raw_payload.get(field)) != _text(row.get(field)):
                raise ValidationError(f"response transition {event_id} {field} differs from raw event")
        transitions[event_id] = {
            "event_id": event_id,
            "attempt_id": attempt_id,
            "session_id": row.get("session_id", ""),
            "parent_event_id": row.get("parent_event_id", ""),
            "before_snapshot_id": row["before_snapshot_id"],
            "after_snapshot_id": row["after_snapshot_id"],
            "before_state": before_state,
            "after_state": after_state,
            "before_state_hash": row["before_state_hash"],
            "after_state_hash": row["after_state_hash"],
            "server_ingest_sequence_no": _integer(
                _require(row, "server_ingest_sequence_no", f"response transition {event_id}"),
                f"response transition {event_id} ingest sequence",
            ),
        }

    for transition in transitions.values():
        parent_id = transition["parent_event_id"]
        if not parent_id:
            continue
        parent = transitions.get(parent_id)
        if parent is None:
            raise ValidationError(f"response transition {transition['event_id']} has missing parent")
        if parent["attempt_id"] != transition["attempt_id"]:
            raise ValidationError(f"response transition {transition['event_id']} parent has another attempt")
        if parent["after_state_hash"] != transition["before_state_hash"]:
            raise ValidationError(
                f"response transition {transition['event_id']} parent after-state hash does not match before-state hash"
            )

    branch_events_by_attempt = {}
    for event_id, row in raw_events.items():
        if row.get("event_type") != "response_branch_resolved":
            continue
        attempt_id = _require(row, "attempt_id", f"branch resolution {event_id}")
        payload = _json(_require(row, "payload", f"branch resolution {event_id}"), f"branch resolution {event_id}")
        branch_events_by_attempt.setdefault(attempt_id, {})[event_id] = payload

    replay_rows = {}
    for row in tables["response_replay"]:
        attempt_id = _require(row, "attempt_id", "response replay")
        if attempt_id in replay_rows:
            raise ValidationError(f"duplicate response replay for attempt {attempt_id}")
        attempt = attempts.get(attempt_id)
        if attempt is None:
            raise ValidationError(f"response replay {attempt_id} has no exported attempt")
        for field in (
            "session_id", "active_response_event_id", "final_response_event_id",
            "response_version", "current_response_snapshot_id", "final_response_snapshot_id",
        ):
            _require_equal(_text(row.get(field)), _text(attempt.get(field)), f"response replay {attempt_id} {field}")
        boundary = _json(_require(row, "formal_capture_boundary", f"response replay {attempt_id}"), f"response replay {attempt_id} formal boundary")
        _require_equal(
            boundary,
            _json(_require(attempt, "formal_capture_boundary", f"attempt {attempt_id}"), f"attempt {attempt_id} formal boundary"),
            f"response replay {attempt_id} formal boundary",
        )
        active_head = row.get("active_response_event_id", "")
        final_head = row.get("final_response_event_id", "")
        active_chain = _chain_for(active_head, transitions)
        final_chain = _chain_for(final_head, transitions)
        _validate_replay_chain(row.get("active_replay_chain", ""), active_chain, f"response replay {attempt_id} active chain")
        _validate_replay_chain(row.get("final_replay_chain", ""), final_chain, f"response replay {attempt_id} final chain")
        for label, head, chain, state_column, hash_column in (
            ("active", active_head, active_chain, "active_state", "active_state_hash"),
            ("final", final_head, final_chain, "final_state", "final_state_hash"),
        ):
            if not head:
                if row.get(state_column) or row.get(hash_column):
                    raise ValidationError(f"response replay {attempt_id} has {label} state without a head")
                continue
            terminal = chain[-1]
            state = _validate_response_state(
                _require(row, state_column, f"response replay {attempt_id}"),
                _require(row, hash_column, f"response replay {attempt_id}"),
                f"response replay {attempt_id} {label} state",
            )
            if state != terminal["after_state"] or row[hash_column] != terminal["after_state_hash"]:
                raise ValidationError(f"response replay {attempt_id} {label} state does not match selected head")
        if boundary:
            required_boundary = {
                "capture_contract_id", "active_response_event_id", "response_version",
                "server_ingest_sequence_no", "student_id",
            }
            if not required_boundary.issubset(boundary):
                raise ValidationError(f"response replay {attempt_id} formal boundary is incomplete")
            if _text(boundary["capture_contract_id"]) != _text(row.get("capture_contract_id")):
                raise ValidationError(f"response replay {attempt_id} formal boundary capture contract differs")
            if _text(boundary["active_response_event_id"]) != _text(active_head):
                raise ValidationError(f"response replay {attempt_id} formal boundary active head differs")
            if _text(boundary["response_version"]) != _text(row.get("response_version")):
                raise ValidationError(f"response replay {attempt_id} formal boundary response version differs")
            if _text(boundary["student_id"]) != _text(row.get("student_id")):
                raise ValidationError(f"response replay {attempt_id} formal boundary student differs")
            boundary_sequence = _integer(
                boundary["server_ingest_sequence_no"],
                f"response replay {attempt_id} formal boundary sequence",
            )
            if active_chain and boundary_sequence < active_chain[-1]["server_ingest_sequence_no"]:
                raise ValidationError(f"response replay {attempt_id} formal boundary precedes active head")
        resolutions = _json(row.get("branch_resolutions", ""), f"response replay {attempt_id} branch resolutions")
        if not isinstance(resolutions, list):
            raise ValidationError(f"response replay {attempt_id} branch resolutions are not a list")
        raw_resolutions = branch_events_by_attempt.get(attempt_id, {})
        supplied_resolution_ids = set()
        for resolution in resolutions:
            if not isinstance(resolution, dict):
                raise ValidationError(f"response replay {attempt_id} has a non-object branch resolution")
            event_id = _require(resolution, "event_id", f"response replay {attempt_id} branch resolution")
            raw_payload = raw_resolutions.get(event_id)
            if raw_payload is None:
                raise ValidationError(f"response replay {attempt_id} branch resolution is absent from raw events")
            candidates = resolution.get("candidate_event_ids")
            selected = resolution.get("selected_event_id")
            if not isinstance(candidates, list) or _text(selected) not in {_text(item) for item in candidates}:
                raise ValidationError(f"response replay {attempt_id} branch resolution selected head is invalid")
            for candidate in candidates:
                transition = transitions.get(_text(candidate))
                if transition is None or transition["attempt_id"] != attempt_id:
                    raise ValidationError(f"response replay {attempt_id} branch resolution candidate is invalid")
            for field in ("operation_id", "candidate_event_ids", "selected_event_id", "expected_version"):
                if resolution.get(field) != raw_payload.get(field):
                    raise ValidationError(f"response replay {attempt_id} branch resolution {field} differs from raw event")
            supplied_resolution_ids.add(event_id)
        if supplied_resolution_ids != set(raw_resolutions):
            raise ValidationError(f"response replay {attempt_id} omits or adds branch resolutions")
        replay_rows[attempt_id] = {"row": row, "boundary": boundary}

    for attempt_id in attempts:
        if attempt_id not in replay_rows:
            raise ValidationError(f"attempt {attempt_id} is missing response replay")

    input_operations_by_session = {}
    branch_resolutions_by_session = {}
    maximum_sequence_by_session = {}
    for row in raw_events.values():
        session_id = row.get("session_id", "")
        if row.get("event_type") == "input_operation":
            input_operations_by_session[session_id] = input_operations_by_session.get(session_id, 0) + 1
        if row.get("event_type") == "response_branch_resolved":
            branch_resolutions_by_session[session_id] = branch_resolutions_by_session.get(session_id, 0) + 1
        sequence = _integer(
            row.get("server_ingest_sequence_no", "0") or 0,
            f"assessment event {row.get('event_id', '')} ingest sequence",
        )
        maximum_sequence_by_session[session_id] = max(maximum_sequence_by_session.get(session_id, 0), sequence)
    transition_counts_by_session = {}
    for transition in transitions.values():
        session_id = transition["session_id"]
        transition_counts_by_session[session_id] = transition_counts_by_session.get(session_id, 0) + 1

    attempts_by_session = {}
    for attempt in attempts.values():
        attempts_by_session.setdefault(attempt.get("session_id", ""), []).append(attempt)
    summary_session_ids = set()
    for row in tables["session_summary"]:
        session_id = _require(row, "session_id", "session summary")
        if session_id in summary_session_ids:
            raise ValidationError(f"duplicate session summary {session_id}")
        summary_session_ids.add(session_id)
        session_attempts = attempts_by_session.get(session_id, [])
        expected = {
            "attempt_count": len(session_attempts),
            "response_transition_count": transition_counts_by_session.get(session_id, 0),
            "input_operation_count": input_operations_by_session.get(session_id, 0),
            "response_branch_resolved_count": branch_resolutions_by_session.get(session_id, 0),
            "active_response_attempt_count": sum(bool(item.get("active_response_event_id")) for item in session_attempts),
            "final_response_attempt_count": sum(bool(item.get("final_response_event_id")) for item in session_attempts),
            "formal_capture_boundary_count": sum(bool(_json(item.get("formal_capture_boundary", "{}"), "attempt formal boundary")) for item in session_attempts),
            "maximum_server_ingest_sequence_no": maximum_sequence_by_session.get(session_id, 0),
        }
        for field, value in expected.items():
            if _text(row.get(field)) != _text(value):
                raise ValidationError(f"session summary {session_id} {field} does not match frozen events")
        for attempt in session_attempts:
            boundary = _json(attempt.get("formal_capture_boundary", "{}"), "attempt formal boundary")
            if boundary:
                if _integer(
                    boundary["server_ingest_sequence_no"],
                    f"session summary {session_id} formal boundary sequence",
                ) > expected["maximum_server_ingest_sequence_no"]:
                    raise ValidationError(f"session summary {session_id} precedes a formal capture boundary")
    if summary_session_ids != set(attempts_by_session):
        raise ValidationError("session summary does not cover exported attempts")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a frozen response replay archive.")
    parser.add_argument("archive", help="path to the complete export ZIP")
    args = parser.parse_args(argv)
    try:
        validate(args.archive)
    except (OSError, zipfile.BadZipFile, ValidationError) as error:
        print(f"response replay validation failed: {error}", file=sys.stderr)
        return 1
    print("response replay validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def standalone_response_replay_script():
    return STANDALONE_RESPONSE_REPLAY_SCRIPT
