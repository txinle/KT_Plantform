"""Frozen identity and evidence rules for the independent teacher capacity v4 run."""
import copy
import hashlib
import json
from pathlib import Path
import re

from capacity_retest import metric_statistics

VERSION = "teacher-capacity-v4"
PROJECT = "kt-school-teacher-capacity-20261018"
ROOT = "/home/ktdata/student-capacity-tests/20261018-teacher-capacity"
PORT = 18086
ORIGIN = f"http://127.0.0.1:{PORT}"
TEACHER_LIMITS = {"advanced": 1000, "status": 300, "search": 500}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def default_manifest(source_sha256):
    c4 = json.loads((Path(__file__).resolve().parents[3] / "deploy/student-capacity-c4.json").read_text())
    return {"version": VERSION, "project": PROJECT, "root": ROOT, "port": PORT,
            "database": "kt_e2e", "data_origin": "fresh_migrations_and_seed",
            "source_archive_sha256": source_sha256,
            "counts": [50, 180], "browsers": ["chromium", "webkit"],
            "generator_location": "school_isolated_container", "parameters": c4["parameters"],
            "teacher_count": 3, "pages_per_teacher": 2, "teacher_cycles": 18,
            "teacher_base_get_limit": 42, "teacher_minimum_samples": 100, "teacher_tail_scope": "full-flow-only",
            "resources": {"slice": "ktteachercapacity.slice", "cpu_quota_percent": 1200,
                          "memory_bytes": 12 * 1024**3, "app_memory_bytes": 10 * 1024**3,
                          "driver_memory_bytes": 2 * 1024**3, "driver_cpus": 2},
            "thresholds": {"login_p95_ms": 10000, "login_p99_ms": 20000,
                           "entry_budget_ms": 30000, "mixed_student_p95_increase": .10},
            "safety": {"monitor_stale_seconds": 30, "minimum_host_headroom_bytes": 2 * 1024**3,
                       "stop_on_oom": True, "maximum_configuration_changes": 2}}


def validate_manifest(value):
    required = {"version": VERSION, "project": PROJECT, "root": ROOT, "port": PORT,
                "database": "kt_e2e", "data_origin": "fresh_migrations_and_seed",
                "counts": [50, 180], "browsers": ["chromium", "webkit"],
                "generator_location": "school_isolated_container", "teacher_count": 3,
                "pages_per_teacher": 2, "teacher_cycles": 18, "teacher_base_get_limit": 42,
                "teacher_minimum_samples": 100, "teacher_tail_scope": "full-flow-only"}
    if any(value.get(k) != v for k, v in required.items()) or not re.fullmatch(
            r"[0-9a-f]{64}", str(value.get("source_archive_sha256", ""))):
        raise ValueError("Only the fresh owned teacher v4 identity and frozen workload are permitted")
    expected = default_manifest(value["source_archive_sha256"])
    for key in ("resources", "thresholds", "safety", "parameters"):
        if value.get(key) != expected[key]:
            raise ValueError("Frozen C4/resource/measurement definition differs: " + key)
    return value


def matrix(manifest):
    validate_manifest(manifest)
    rows = [{"stage": "qualification", "phase": "candidate", "mode": "full-flow", "count": 50,
             "window_seconds": 10, "bootstrap": "cold", "teacher": True, "round": 1}]
    for phase, repeats, stage in (("baseline", 1, "baseline"), ("candidate", 2, "candidate-entry")):
        for repeat in range(1, repeats + 1):
            for window in (60, 30, 10):
                for bootstrap in ("warm", "cold"):
                    for teacher in (False, True):
                        rows.append({"stage": stage, "phase": phase, "mode": "entry", "count": 180,
                                     "window_seconds": window, "bootstrap": bootstrap,
                                     "teacher": teacher, "round": repeat})
    for repeat in (1, 2):
        for bootstrap in ("warm", "cold"):
            for teacher in (False, True):
                rows.append({"stage": "candidate-full-flow", "phase": "candidate", "mode": "full-flow",
                             "count": 180, "window_seconds": 10, "bootstrap": bootstrap,
                             "teacher": teacher, "round": repeat})
    return [{**row, "browser_count": 2, "protocol_count": row["count"] - 2,
             "cell_id": digest(row)[:20]} for row in rows]


def arrivals(account_ids, browser_ids, *, window_seconds):
    if len(account_ids) not in (50, 180) or len(set(account_ids)) != len(account_ids) or (
            len(browser_ids) != 2 or len(set(browser_ids)) != 2 or set(browser_ids) - set(account_ids)
            or window_seconds not in (10, 30, 60)):
        raise ValueError("One complete 50/180 roster and two unique included browser actors are required")
    return [{"account_id": account, "driver": "browser" if account in browser_ids else "protocol",
             "login_offset_ms": 1000 * window_seconds * index / (len(account_ids) - 1)}
            for index, account in enumerate(account_ids)]


def teacher_metrics(records, *, descriptive=False):
    rows = [r for r in records if r.get("during_student_pressure") is True]
    metrics = {key: metric_statistics([r.get("duration_ms") if r.get("success") else None
                                      for r in rows if r.get("category") == key], limit=limit)
               for key, limit in TEACHER_LIMITS.items()}
    if descriptive:
        return {"criterion": "descriptive_only_entry_window", "accepted": None,
                "metrics": {key: {name: m[name] for name in ("planned", "observed", "missing", "max_ms")}
                            for key, m in metrics.items()},
                "raw_durations_ms": {key: [r.get("duration_ms") if r.get("success") else None
                                          for r in rows if r.get("category") == key] for key in TEACHER_LIMITS},
                "request_failures": sum(r.get("success") is not True for r in rows),
                "after_pressure_samples": len(records) - len(rows)}
    return {"metrics": metrics, "after_pressure_samples": len(records) - len(rows),
            "successful_gets": sum(r.get("success") is True for r in rows),
            "accepted": all(r.get("success") is True for r in rows) and all(m["planned"] >= 100 and m["missing"] == 0
                            and m["p95_ms"] is not None and m["p95_ms"] <= TEACHER_LIMITS[key]
                            for key, m in metrics.items())}


def evaluate(cell, student_records, teacher_records, verification, monitor):
    ids = [r.get("account_id") for r in student_records]
    issues = []
    if len(ids) != cell["count"] or len(set(ids)) != cell["count"] or any(
            r.get("status") != "completed" for r in student_records):
        issues.append("student_denominator_incomplete")
    if (verification.get("integrity_passed") is not True or verification.get("planned") != cell["count"]
            or verification.get("workflow_complete") is not True
            or monitor.get("monitor_ok") is not True or monitor.get("generator_valid") is not True
            or monitor.get("collector_complete") is not True or monitor.get("log_coverage_ok") is not True
            or monitor.get("non_project_unchanged") is not True or monitor.get("oom") is not False):
        issues.append("measurement_invalid")
    if cell["mode"] == "full-flow" and verification.get("stats_ready") is not True:
        issues.append("statistics_not_ready")
    protocol = [r for r in student_records if r.get("driver") == "protocol"]
    login = metric_statistics([r.get("login_ms") for r in protocol], limit=10000)
    if login["planned"] != cell["protocol_count"] or login["missing"] or (
            login["p95_ms"] is None or login["p95_ms"] > 10000
            or login["p99_ms"] is None or login["p99_ms"] > 20000):
        issues.append("login_threshold")
    if any(r.get("login_ms") is None or r["login_ms"] > 30000 for r in protocol):
        issues.append("entry_common_budget")
    teachers = teacher_metrics(teacher_records, descriptive=cell["mode"] == "entry")
    if cell["teacher"] and cell["mode"] == "entry" and teachers["request_failures"]:
        issues.append("teacher_request_failure")
    if cell["teacher"] and cell["mode"] == "full-flow" and not teachers["accepted"]:
        issues.append("teacher_tail_population_or_threshold")
    return {"cell_id": cell["cell_id"], "passed": not issues, "issues": issues,
            "counts": {"planned": cell["count"], "observed": len(ids)},
            "login": login, "teacher": teachers, "browser_observations": [r for r in student_records
                                                                                 if r.get("driver") == "browser"]}


def mixed_comparison(reference, candidate):
    issues = []
    if not reference.get("binding") or reference.get("binding") != candidate.get("binding"):
        issues.append("environment_binding_differs")
    for metric in ("save", "navigation", "submission"):
        before, after = (r.get("timings", {}).get(metric, {}).get("p95_ms") for r in (reference, candidate))
        if not isinstance(before, (int, float)) or before <= 0 or not isinstance(after, (int, float)) or after > before * 1.10:
            issues.append("student_p95_increase:" + metric)
    return {"passed": not issues, "issues": issues}


def build_plan(manifest, cell, fixture, fixture_sha256):
    import student_capacity_protocol_v3 as protocol
    validate_manifest(manifest)
    if cell not in matrix(manifest):
        raise ValueError("Cell is not in the frozen v4 matrix")
    ids = [a["account_id"] for a in fixture["accounts"][:cell["count"]]]
    browsers = [ids[0], ids[-1]]
    # Reuse only the frozen trajectory builder, never the historical controller.
    plan = protocol.build_plan(fixture, mode="login" if cell["mode"] == "entry" else "full-flow",
                               count=cell["count"], run_id=cell["cell_id"], fixture_sha256=fixture_sha256,
                               base_url=ORIGIN, browser_account_ids=browsers,
                               workload_profile=protocol.HISTORICAL_PROFILE)
    for row, arrival in zip(plan["roster"], arrivals(ids, browsers, window_seconds=cell["window_seconds"])):
        row.update(arrival)
    plan.update(capacity_version=VERSION, workload_profile=protocol.WORKLOAD_PROFILE,
                login_window_ms=cell["window_seconds"] * 1000, bootstrap=cell["bootstrap"],
                manifest_sha256=digest(manifest), cell=copy.deepcopy(cell), same_DUT=True,
                measurement_version=VERSION, metric_schema_version="teacher-capacity-metrics-v4")
    plan.pop("plan_id", None)
    plan["plan_id"] = digest(plan)
    return plan
