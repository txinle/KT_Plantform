"""One visit projection for research rows and the teacher's session timeline."""

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils.dateparse import parse_datetime


def as_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return parse_datetime(str(value))


def research_time(value):
    value = as_datetime(value)
    if value is None or value.tzinfo is None:
        return ""
    return value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S.%f")[:-3]


def build_visits(visit_records, events, question_refs):
    by_attempt = defaultdict(list)
    entry_events = defaultdict(list)
    for event in events:
        if event.event_type == "item_entered":
            entry_events[event.attempt_id].append(event)
    visits = []
    for record in visit_records:
        candidates = entry_events[record.attempt_id]
        entry = next((event for event in candidates if str(event.payload.get("visit_id", "")) == str(record.pk)), None)
        if entry is None:
            # Old server visit and entry records are emitted together in order.
            index = len(by_attempt[record.attempt_id])
            entry = candidates[index] if index < len(candidates) else None
        started = entry.server_committed_at if entry else record.server_received_at
        item = {
            "key": str(record.pk), "session_id": str(record.session_id), "attempt_id": str(record.attempt_id),
            "question_ref": question_refs.get(str(record.attempt.assignment.snapshot_id), ""),
            "visit_index": record.visit_index, "sequence": record.event_sequence,
            "clicked_at": record.client_occurred_at.isoformat() if record.client_occurred_at else "",
            "entered_at": started.isoformat(), "left_at": "", "duration_seconds": None,
            "problem": "" if record.client_occurred_at else "未记录点击时间",
            "navigation_id": str(entry.correlation_id) if entry and entry.correlation_id else "",
            "entry_event_id": str(entry.event_id) if entry else "",
        }
        by_attempt[record.attempt_id].append(item)
        visits.append(item)
    for event in events:
        if event.event_type != "item_left":
            continue
        item = match_visit(event, visits)
        if item:
            occurred = event.estimated_occurred_at_utc or event.client_wall_time_utc or event.server_committed_at
            if not item["left_at"]:
                item["left_at"] = occurred.isoformat()
                elapsed = (occurred - as_datetime(item["entered_at"])).total_seconds()
                if elapsed >= 0:
                    item["duration_seconds"] = elapsed
                else:
                    item["problem"] = "；".join(filter(None, (item["problem"], "进入与离开时钟不一致")))
    return visits


def match_visit(event, visits):
    candidates = [item for item in visits if item["session_id"] == str(event.session_id) and item["attempt_id"] == str(event.attempt_id)]
    explicit = event.payload.get("visit_id")
    if explicit is not None:
        return next((item for item in candidates if item["key"] == str(explicit)), None)
    correlation = str(event.correlation_id) if event.correlation_id else ""
    if correlation:
        matched = next((item for item in candidates if item["navigation_id"] == correlation or item["entry_event_id"] == correlation), None)
        if matched:
            return matched
    # Never use delayed server receipt as the time of a client operation.
    occurred = event.estimated_occurred_at_utc
    if event.actor_source == "server":
        occurred = event.server_committed_at
    if occurred is None:
        return None
    eligible = [item for item in candidates if as_datetime(item["entered_at"]) <= occurred]
    if not eligible:
        return None
    item = max(eligible, key=lambda value: value["entered_at"])
    if item["left_at"] and occurred > as_datetime(item["left_at"]):
        return None
    return item
