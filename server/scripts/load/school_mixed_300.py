#!/usr/bin/env python3
"""Authenticated assessment / read isolation acceptance, stdlib only.

Run baseline and mixed separately on fresh kt_e2e fixture manifests. An abbreviated
smoke run writes observations but cannot pass the formal 30-minute criteria.
"""
import argparse
import concurrent.futures
import json
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assessment_300 import Runner, StudentState, percentile
from capture_protocol import CaptureProtocolMixin, CONTRACT, evidence_files
from school_short_storm import ShortRunner


THRESHOLDS = {
    "save_draft": 1, "submit_item": 2, "enter_item": 1, "event_batch": 1, "finish": 2,
    "teacher_first": 1.5, "teacher_page": 1, "teacher_search": 1,
    "student_hot": 1, "student_cold": 2, "student_detail_hot": 1, "student_detail_cold": 2, "comparison_ready": 30,
}
CORE_METRICS = ("save_draft", "submit_item", "enter_item", "event_batch", "finish")
CAPTURE_P99_THRESHOLDS = {"enter_item": 2, "save_draft": 2, "event_batch": 2, "submit_item": 5, "finish": 5}


def start_gate(phase):
    row = phase.get('metrics', {}).get('start', {})
    return (phase.get('student_count') == 300 and row.get('count') == 300
            and row.get('p95_seconds', float('inf')) <= 5
            and row.get('p99_seconds', float('inf')) <= 10)


def evaluate(baseline, mixed, observation):
    metrics = mixed.get("metrics", {})
    latency = {
        key: bool(metrics.get(key, {}).get("count", 0))
        and metrics[key].get("p95_seconds", float("inf")) <= limit
        for key, limit in THRESHOLDS.items()
    }
    capture_latency = {phase_name: {
        key: bool(phase.get('metrics', {}).get(key, {}).get('count', 0))
        and phase['metrics'][key].get('p95_seconds', float('inf')) <= THRESHOLDS[key]
        and phase['metrics'][key].get('p99_seconds', float('inf')) <= tail
        for key, tail in CAPTURE_P99_THRESHOLDS.items()}
        for phase_name, phase in (('baseline', baseline), ('mixed', mixed))}
    degradation = {}
    for key in CORE_METRICS:
        before = baseline.get("metrics", {}).get(key, {}).get("p95_seconds")
        after = metrics.get(key, {}).get("p95_seconds")
        if before is not None and before > 0 and after is not None:
            degradation[key] = round(after / before - 1, 6)
    checks = {
        "both_groups_real_capture_protocol": all(
            phase.get('capture_protocol', {}).get('contract') == CONTRACT
            and phase['capture_protocol'].get('all_events_acknowledged') is True
            and phase['capture_protocol'].get('completed_sessions') == 300
            and all(phase['capture_protocol'].get('scenarios', {}).get(kind, 0) >= 300
                    for kind in ('single_choice', 'true_false', 'multiple_choice', 'fill_blank', 'short_answer',
                                 'ime_protocol_simulation', 'queued_replay_reload_protocol', 'writer_lease_reacquire',
                                 'required_check', 'hint_observed_protocol', 'authorized_feedback_protocol'))
            for phase in (baseline, mixed)),
        "full_duration": all(phase.get("duration_seconds", 0) >= 1800 for phase in (baseline, mixed)),
        "300_students_and_5_teachers": mixed.get("student_count") == 300 and mixed.get("teacher_count") == 5,
        "all_students_completed": all(phase.get("completed_student_count") == 300 for phase in (baseline, mixed)),
        "all_students_started_within_tail_limits": all(start_gate(phase) for phase in (baseline, mixed)),
        "no_workflow_failures": all(phase.get("workflow_failures", 0) == 0 for phase in (baseline, mixed)),
        "teacher_scope_switches_and_exports": mixed.get("scope_switches", 0) >= 25 and mixed.get("exports_created", 0) >= 5 and mixed.get("exports_completed", 0) >= 5,
        "request_error_rate_below_0_1_percent": all(phase.get("requests", 0) > 0 and phase.get("errors", 0) / phase["requests"] < .001 for phase in (baseline, mixed)),
        "latency": all(latency.values()),
        "both_groups_capture_p95_and_p99": all(all(values.values()) for values in capture_latency.values()),
        "save_and_submit_p95_degradation_at_most_20_percent": all(key in degradation and degradation[key] <= .2 for key in ('save_draft','submit_item')),
        "zero_observed_oom": bool(observation) and observation.get("source") in ("docker_inspect", "docker_events") and observation.get("oom_kills") == 0 and observation.get("observed_duration_seconds", 0) >= mixed.get("duration_seconds", 0),
        "observation_covers_workload": bool(observation) and observation.get("started_at", float("inf")) <= mixed.get("started_at", 0) and observation.get("ended_at", 0) >= mixed.get("ended_at", float("inf")),
        "no_swap_growth": bool(observation) and observation.get("swap_start_bytes") is not None and observation.get("swap_end_bytes") is not None and observation["swap_end_bytes"] <= observation["swap_start_bytes"],
        "background_backlog_drained": bool(observation) and bool(observation.get("backlog_samples")) and all(value == 0 for value in observation["backlog_samples"][-1]["counts"].values()),
        "teacher_new_data_ready_within_30_seconds": bool(observation) and observation.get("teacher_freshness", {}).get("terminal_count") == 300 and observation["teacher_freshness"].get("initial_count") == 300 and observation["teacher_freshness"].get("initial_p95_seconds") is not None and observation["teacher_freshness"]["initial_p95_seconds"] <= 30,
        "teacher_latest_source_ready_within_30_seconds": bool(observation) and observation.get("teacher_freshness", {}).get("current_count") == 300 and observation["teacher_freshness"].get("latest_count") == 300 and observation["teacher_freshness"].get("latest_p95_seconds") is not None and observation["teacher_freshness"]["latest_p95_seconds"] <= 30,
    }
    return {"passed": all(checks.values()), "checks": checks, "latency_checks": latency,
            'capture_latency_checks': capture_latency,
            "core_p95_degradation": degradation, "thresholds_p95_seconds": THRESHOLDS}


class MixedRunner(ShortRunner):
    # Preserve the mixed workload's existing start metric and result reads.
    start = Runner.start
    def __init__(self, *args, course_id, experiment_id, question_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.learning_path = f"/api/v1/student/courses/{course_id}/learning/"
        self.detail_prefix = f"{self.learning_path}experiments/{experiment_id}/questions/"

    def after_finish(self, state):
        started = time.monotonic()
        result_path = f"/api/v1/student/tasks/{self.task_id}/results/"
        _, payload, seconds = self.request(state, result_path)
        self.record("student_cold", seconds)
        completed_at = payload.get("completed_at")
        for elapsed in (5, 15, 30):
            if payload.get("comparison_status") not in ("pending", "updating"):
                break
            time.sleep(max(0, elapsed - (time.monotonic() - started)))
            _, payload, seconds = self.request(state, result_path + "?status_only=1")
            self.record("student_result_status", seconds)
            if payload.get("comparison_status") == "ready":
                _, payload, _ = self.request(state, result_path)
                break
        if payload.get("comparison_status") != "ready":
            raise RuntimeError("comparison not ready")
        generated_at = payload.get("comparison_generated_at")
        if not completed_at or not generated_at or datetime.fromisoformat(generated_at.replace("Z", "+00:00")) < datetime.fromisoformat(completed_at.replace("Z", "+00:00")):
            raise RuntimeError("comparison freshness evidence absent")
        freshness = (datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                     - datetime.fromisoformat(completed_at.replace("Z", "+00:00"))).total_seconds()
        self.record("comparison_ready", freshness)
        # Retained fixture question_id identifies the bank row, not the frozen
        # snapshot consumed by the learning endpoint. Use this student's DTO.
        items = payload.get("items") or []
        question_id = items[0].get("question_id") if items else None
        if type(question_id) is not int or question_id <= 0:
            raise RuntimeError("authorized frozen question identity absent")
        detail_path = f"{self.detail_prefix}{question_id}/"
        _, _, seconds = self.request(state, detail_path)
        self.record("student_detail_cold", seconds)
        for _ in range(2):
            _, _, seconds = self.request(state, result_path)
            self.record("student_hot", seconds)
            _, _, seconds = self.request(state, detail_path)
            self.record("student_detail_hot", seconds)


class ProtocolMixedRunner(CaptureProtocolMixin, MixedRunner):
    pass


def phase(args, manifest, files=None):
    import resource
    client_cpu_started = time.process_time()
    warmup_started = time.time()
    mixed = args.phase == "mixed"
    # Both phases use identical student writes and completed-result reads.
    # The mixed phase adds only teacher activity and its associated export work.
    runner_type = ProtocolMixedRunner if manifest.get('capture_protocol') == CONTRACT else MixedRunner
    kwargs = {key: manifest[key] for key in ("course_id", "experiment_id", "question_id")}
    # Login and synchronized start take place before the timed 30-minute workload.
    runner = runner_type(args.base_url, manifest["task_id"], args.timeout,
                         item_interval=args.duration / manifest["item_count"], **kwargs)
    runner.protocol_course_id, runner.protocol_experiment_id = manifest['course_id'], manifest['experiment_id']
    if files is not None:
        files['runner'] = runner
        runner.attempt_output = files['attempts']
    students = [StudentState(name, manifest["password"]) for name in manifest["usernames"]]
    teachers = [StudentState(name, manifest["password"]) for name in manifest.get("teacher_usernames", [])] if mixed else []
    runner.phase("login", students + teachers, runner.login, 6, synchronized=False)
    login_completed = time.time()
    valid_students = [state for state in students if not getattr(state, 'workflow_failed', False)]
    runner.phase("start", valid_students, runner.start, len(valid_students))
    valid_students = [state for state in valid_students if not getattr(state, 'workflow_failed', False)]
    valid_teachers = [state for state in teachers if not getattr(state, 'workflow_failed', False)]
    started = time.monotonic()
    started_wall = time.time()
    deadline = started + args.duration
    completed = 0
    workflow_failures = len(runner.errors)
    scope_switches = 0
    exports_created = 0
    exports_completed = 0
    guard = threading.Lock()

    def student_work(state):
        nonlocal completed, workflow_failures
        try:
            runner.answer(state)
            with guard:
                completed += 1
        except Exception as error:
            with guard:
                workflow_failures += 1
            with runner.lock:
                runner.errors.append({'phase': 'answer', 'error': type(error).__name__})

    def teacher_work(state):
        nonlocal workflow_failures, scope_switches, exports_created, exports_completed
        first = True
        cycle = 0
        job_id = None
        job_started = None
        status_checks = 0
        export_completed = False
        history = manifest["history_experiment_id"]
        current = manifest["experiment_id"]
        scopes = [
            {"experiment_ids": history, "class_id": manifest["class_id"]},
            {"experiment_ids": current, "class_id": manifest["class_id"]},
            {"experiment_ids": f"{history},{current}", "class_id": manifest["class_id"]},
            {"experiment_ids": history}, {"experiment_ids": current},
        ]
        try:
            while time.monotonic() < deadline:
                query = urlencode(scopes[cycle % len(scopes)])
                prefix = f"/api/v1/teacher/courses/{manifest['course_id']}/data/"
                _, _, seconds = runner.request(state, f"{prefix}overview/?{query}")
                runner.record("teacher_scope_switch", seconds)
                with guard:
                    scope_switches += 1
                _, _, seconds = runner.request(state, f"{prefix}advanced/?{query}&table=sessions&page=1")
                runner.record("teacher_first" if first else "teacher_page", seconds)
                first = False
                _, _, seconds = runner.request(state, f"{prefix}advanced/?{query}&table=sessions&page=2")
                runner.record("teacher_page", seconds)
                _, _, seconds = runner.request(state, f"{prefix}advanced/?{query}&table=responses&page=2")
                runner.record("teacher_page", seconds)
                search_query = urlencode({**scopes[cycle % len(scopes)], "q": manifest["usernames"][cycle % len(students)]})
                _, _, seconds = runner.request(state, f"{prefix}advanced/students/?{search_query}")
                runner.record("teacher_search", seconds)
                if cycle == 4:
                    _, payload, seconds = runner.request(state, f"{prefix}export-jobs/", method="POST", body={
                        "package": "experiment", "confirm_identified": True,
                        "scope": {"scope_version": 2, "experiment_id": history, "class_mode": "single", "class_id": manifest["class_id"]},
                    })
                    runner.record("export_create", seconds)
                    job_id = payload["job_id"]
                    job_started = time.monotonic()
                    with guard:
                        exports_created += 1
                if (job_id and not export_completed and status_checks < 3
                        and time.monotonic() - job_started >= (5, 15, 30)[status_checks]):
                    _, payload, seconds = runner.request(state, f"{prefix}export-jobs/{job_id}/")
                    status_checks += 1
                    runner.record("export_status", seconds)
                    if payload.get("status") == "failed":
                        raise RuntimeError("Export failed")
                    if payload.get("status") == "succeeded":
                        export_completed = True
                        with guard:
                            exports_completed += 1
                cycle += 1
                time.sleep(min(args.teacher_interval, max(0, deadline - time.monotonic())))
            if job_id and not export_completed:
                # One explicit visit to the job history at the end of the workload.
                _, payload, seconds = runner.request(state, f"{prefix}export-jobs/{job_id}/")
                runner.record("export_status_manual", seconds)
                if payload.get("status") == "succeeded":
                    export_completed = True
                    with guard:
                        exports_completed += 1
            if not export_completed:
                raise RuntimeError("Export did not finish during workload")
        except Exception as error:
            with guard:
                workflow_failures += 1
            with runner.lock:
                runner.errors.append({'phase': 'teacher', 'error': type(error).__name__})

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(valid_students) + len(valid_teachers))) as pool:
        futures = [pool.submit(student_work, state) for state in valid_students]
        futures += [pool.submit(teacher_work, state) for state in valid_teachers]
        for future in futures:
            future.result()
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    result = {
        "phase": args.phase, "run_id": manifest["run_id"],
        "started_at": started_wall, "ended_at": time.time(),
        "warmup_started_at": warmup_started, "login_completed_at": login_completed,
        "client_cpu_seconds": round(time.process_time() - client_cpu_started, 3),
        "client_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "client_duration_including_warmup_seconds": round(time.time() - warmup_started, 3),
        "duration_seconds": round(time.monotonic() - started, 3),
        "student_count": len(students), "teacher_count": len(teachers),
        "completed_student_count": completed,
        "requests": runner.request_count, "errors": runner.request_errors,
        "workflow_failures": workflow_failures,
        "workflow_errors": runner.errors,
        "scope_switches": scope_switches, "exports_created": exports_created, "exports_completed": exports_completed,
        "metrics": {key: {"count": len(values), "p50_seconds": round(statistics.median(values), 3),
                          "p95_seconds": percentile(values, .95), "p99_seconds": percentile(values, .99),
                          "max_seconds": round(max(values), 3)}
                    for key, values in runner.metrics.items()},
    }
    result['attempt_summary'] = runner.attempt_summary()
    if isinstance(runner, ProtocolMixedRunner):
        result['capture_protocol'] = runner.protocol_summary()
    if files is not None:
        files['complete'] = workflow_failures == 0
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", required=True, help="Secret fixture file generated in isolated kt_e2e")
    parser.add_argument("--phase", choices=("baseline", "mixed"), required=True)
    parser.add_argument("--duration", type=int, default=1800)
    parser.add_argument("--teacher-interval", type=float, default=10)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--baseline-result")
    parser.add_argument("--oom-evidence")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not 0 < args.timeout <= 35:
        raise SystemExit('Request timeout must be at most 35 seconds')
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    if manifest.get("database") != "kt_e2e" or len(manifest.get("usernames", [])) != 300:
        raise SystemExit("Requires isolated kt_e2e fixture with exactly 300 students")
    if args.phase == "mixed" and len(manifest.get("teacher_usernames", [])) != 5:
        raise SystemExit("Mixed phase requires exactly five teacher fixture accounts")
    if urlparse(args.base_url).scheme not in ("http", "https") or args.duration <= 0 or args.teacher_interval <= 0:
        raise SystemExit("Invalid URL or workload duration")
    if args.phase == "mixed" and not args.baseline_result:
        raise SystemExit("Mixed phase requires --baseline-result")
    if manifest.get('capture_protocol') not in (None, CONTRACT):
        raise SystemExit('Unknown capture protocol contract')
    try:
        with evidence_files(args.manifest, args.output) as files:
            result = phase(args, manifest, files)
            if args.phase == "mixed":
                baseline = json.loads(Path(args.baseline_result).read_text(encoding="utf-8"))
                observation = json.loads(Path(args.oom_evidence).read_text(encoding="utf-8")) if args.oom_evidence else None
                result["acceptance"] = evaluate(baseline, result, observation)
            json.dump(result, files['result'], ensure_ascii=False, indent=2)
    except Exception as error:
        raise SystemExit(f"Load fixture or transport failed ({type(error).__name__}); details withheld") from None
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.phase == "mixed":
        raise SystemExit(0 if result["acceptance"]["passed"] else 1)


if __name__ == "__main__":
    main()
