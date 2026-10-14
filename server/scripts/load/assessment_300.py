#!/usr/bin/env python3
"""Run the formal 300-student HTTP acceptance test with Python stdlib only."""

import argparse
import concurrent.futures
import http.cookiejar
import hashlib
import json
import math
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StudentState:
    username: str
    password: str
    opener: object = field(init=False)
    cookies: object = field(init=False)
    session_id: int | None = None
    assignment_ids: list[int] = field(default_factory=list)
    finish_payload: dict = field(default_factory=dict)

    def __post_init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )


def percentile(values, percent):
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percent) - 1))
    return round(ordered[index], 3)


class Runner:
    def __init__(self, base_url, task_id, timeout, *, item_interval=0):
        self.base_url = base_url.rstrip("/")
        self.task_id = task_id
        self.timeout = timeout
        self.metrics = {}
        self.errors = []
        self.lock = threading.Lock()
        self.item_interval = item_interval
        self.request_count = 0
        self.request_errors = 0
        self.request_attempts = []
        self.request_context = threading.local()
        self.attempt_output = None
        self.item_arrivals = []

    def before_item(self, state, index):
        now = time.monotonic()
        if index == 0:
            state.load_item_started_at = now
        offset = index * self.item_interval
        with self.lock:
            self.item_arrivals.append({'item_index': index, 'planned_offset_seconds': offset,
                'actual_offset_seconds': round(now - state.load_item_started_at, 6),
                'send_delay_seconds': round(max(0, now - state.load_item_started_at - offset), 6)})

    def attempt_summary(self):
        operations = {}
        for row in self.request_attempts:
            operations.setdefault(row['operation_id'], []).append(row)
        count = len(operations)
        first = sum(rows[0]['successful'] for rows in operations.values())
        retried = sum(len(rows) > 1 for rows in operations.values())
        recovered = sum(not rows[0]['successful'] and rows[-1]['successful'] for rows in operations.values())
        return {'operation_count': count, 'first_attempt_success_rate': first / count if count else None,
            'retry_rate': retried / count if count else None, 'recovered_operations': recovered,
            'failed_operations': sum(not rows[-1]['successful'] for rows in operations.values()),
            'attempt_latency': {'count': len(self.request_attempts),
                'p50_seconds': percentile([r['elapsed_seconds'] for r in self.request_attempts], .5),
                'p95_seconds': percentile([r['elapsed_seconds'] for r in self.request_attempts], .95),
                'p99_seconds': percentile([r['elapsed_seconds'] for r in self.request_attempts], .99),
                'max_seconds': max((r['elapsed_seconds'] for r in self.request_attempts), default=None)}}

    def _csrf(self, state):
        return next(
            (cookie.value for cookie in state.cookies if cookie.name == "csrftoken"),
            "",
        )

    def request(self, state, path, *, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request_id = uuid.uuid4().hex
        context = getattr(self.request_context, 'current', {})
        headers = {"Accept": "application/json", "X-Request-ID": request_id}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if method != "GET":
            headers["X-CSRFToken"] = self._csrf(state)
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        started = time.perf_counter()
        row = {'request_id': request_id, 'operation_id': context.get('operation_id', request_id),
            'attempt': context.get('attempt', 1), 'method': method,
            'path': re.sub(r'/\d+(?=/|$)', '/:id', path.split('?', 1)[0]),
            'sent_at': time.time(), 'http_status': None, 'error_type': None, 'successful': False}
        row['student_index'] = getattr(state, 'load_student_index', None)
        if method == 'POST' and re.fullmatch(r'/api/v1/assessment/process/attempts/\d+/writer-lease/(renew|acquire)/', path):
            client = body.get('client_instance_id') if isinstance(body, dict) else None
            if isinstance(client, str) and client:
                row['writer_client_sha256'] = hashlib.sha256(client.encode()).hexdigest()
                row['lease_scope_sha256'] = hashlib.sha256(path.rsplit('/', 2)[0].encode()).hexdigest()
                if path.endswith('/renew/'):
                    row['lease_cached_valid_until'] = getattr(state, 'browser_lease_cached_valid_until', None)
        with self.lock:
            self.request_count += 1
        try:
            with state.opener.open(request, timeout=self.timeout) as response:
                row['http_status'] = response.status
                application = response.headers.get('X-Capture-Seconds', '')
                row['application_seconds'] = float(application) if re.fullmatch(r'\d+\.\d+', application) else None
                payload = response.read()
                content_type = response.headers.get("Content-Type", "")
                parsed = json.loads(payload) if "json" in content_type else payload
                if path.startswith('/api/v1/') and 200 <= response.status < 300 and response.status != 204:
                    if (not isinstance(parsed, dict) or set(parsed) != {'data', 'meta'}
                            or parsed['meta'] != {'request_id': response.headers.get('X-Request-ID')}
                            or not response.headers.get('X-Request-ID')):
                        raise ValueError('API response envelope or request identity mismatch')
                    parsed = parsed['data']
                row['successful'] = response.status < 400
                return response.status, parsed, time.perf_counter() - started
        except urllib.error.HTTPError as error:
            row['http_status'] = error.code
            row['error_type'] = 'HTTPError'
            with self.lock:
                self.request_errors += 1
            raise RuntimeError(f"HTTP {error.code}") from None
        except Exception as error:
            row['error_type'] = type(error.reason).__name__ if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException) else type(error).__name__
            with self.lock:
                self.request_errors += 1
            raise RuntimeError("HTTP transport or response decoding failure") from None
        finally:
            row['elapsed_seconds'] = round(time.perf_counter() - started, 6)
            row['completed_at'] = time.time()
            row['server_error_code'] = getattr(state, 'browser_last_error_code', None) if not row['successful'] else None
            with self.lock:
                self.request_attempts.append(row)
                state.browser_last_request_attempt = row.copy()
                if self.attempt_output is not None:
                    self.attempt_output.write(json.dumps(row) + '\n')
                    self.attempt_output.flush()

    def record(self, metric, seconds):
        with self.lock:
            self.metrics.setdefault(metric, []).append(seconds)

    def login(self, state):
        status, token, _ = self.request(state, "/api/v1/auth/csrf/")
        if status != 200:
            raise RuntimeError(f"csrf returned {status}")
        started = time.perf_counter()
        status, _, _ = self.request(
            state,
            "/api/v1/auth/login/",
            method="POST",
            body={"username": state.username, "password": state.password},
        )
        self.record("login", time.perf_counter() - started)
        if status != 200:
            raise RuntimeError(f"login returned {status}")

    def start(self, state):
        status, payload, seconds = self.request(
            state,
            f"/api/v1/student/tasks/{self.task_id}/start/",
            method="POST",
            body={},
        )
        self.record("start", seconds)
        if status not in (200, 201):
            raise RuntimeError(f"start returned {status}")
        state.session_id = int(payload["session_id"])
        assignment_id = int(payload["question_url"].rstrip("/").split("/")[-1])
        state.assignment_ids = [assignment_id]

    def entry_material(self, state, reason='navigation_click'):
        if not hasattr(state, 'browser_page_started'):
            state.browser_page_started = time.monotonic()
            state.browser_page_origin = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        return {'reason': reason, 'navigation_id': str(uuid.uuid4()),
            'client_occurred_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'client_monotonic_ms': round((time.monotonic() - state.browser_page_started) * 1000, 3),
            'client_time_origin': state.browser_page_origin}

    def answer(self, state):
        client_instance_id = str(uuid.uuid4())
        sequence = 0
        next_assignment = state.assignment_ids[0]
        visited = set()
        finish_url = ""
        while next_assignment not in visited:
            item_started = time.monotonic()
            self.before_item(state, len(visited))
            visited.add(next_assignment)
            status, payload, seconds = self.request(
                state,
                f"/api/v1/student/sessions/{state.session_id}/questions/{next_assignment}/",
                method='POST', body=self.entry_material(state),
            )
            self.record("enter_item", seconds)
            if status != 200:
                raise RuntimeError(f"question entry returned {status}")
            attempt_id = int(payload["assignment"]["attempt_id"])
            finish_url = payload["actions"]["finish_url"]
            sequence += 1
            now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
            event = {
                "client_instance_id": client_instance_id,
                "events": [{
                    "event_type": "activity_heartbeat",
                    "event_id": str(uuid.uuid4()),
                    "event_version": 1,
                    "session_id": state.session_id,
                    "attempt_id": attempt_id,
                    "client_sequence_no": sequence,
                    "correlation_id": None,
                    "client_wall_time_utc": now,
                    "client_monotonic_ms": str(sequence * 1000),
                    "client_time_origin": now,
                    "time_sync_id": None,
                    "payload": {
                        "attempt_id": attempt_id,
                        "document_visible": True,
                        "window_focused": True,
                        "last_device_interaction_age_ms": 0,
                    },
                    "quality_flags": [],
                    "app_version": "assessment-300-load-v1",
                }],
            }
            status, _, seconds = self.request(
                state,
                payload["process"]["event_batch_url"],
                method="POST",
                body=event,
            )
            self.record("event_batch", seconds)
            if status != 200:
                raise RuntimeError(f"event batch returned {status}")
            status, _, seconds = self.request(
                state,
                payload["actions"]["save_draft_url"],
                method="POST",
                body={"answer": "A"},
            )
            self.record("save_draft", seconds)
            if status != 200:
                raise RuntimeError(f"save returned {status}")
            status, _, seconds = self.request(
                state,
                payload["actions"]["submit_url"],
                method="POST",
                body={"submission_token": str(uuid.uuid4())},
            )
            self.record("submit_item", seconds)
            if status != 200:
                raise RuntimeError(f"submit returned {status}")
            next_assignment = next(
                (
                    int(row["assignment_id"])
                    for row in payload["navigation"]
                    if int(row["assignment_id"]) not in visited
                ),
                next_assignment,
            )
            self.after_item(state)
            remaining = self.item_interval - (time.monotonic() - item_started)
            if remaining > 0:
                time.sleep(remaining)
        status, finish_payload, seconds = self.request(
            state, finish_url, method="POST", body={}
        )
        state.finish_payload = finish_payload
        self.record("finish", seconds)
        if status != 200:
            raise RuntimeError(f"finish returned {status}")
        self.after_finish(state)

    def after_item(self, state):
        """Hook for the mixed-load harness; the assessment-only run adds no work."""

    def after_finish(self, state):
        """Observe post-submission results when the mixed harness requests it."""

    def phase(self, name, states, operation, workers, *, synchronized=True):
        barrier = threading.Barrier(len(states)) if synchronized else None

        def invoke(state):
            try:
                if barrier is not None:
                    barrier.wait(timeout=30)
                operation(state)
            except Exception as error:
                with self.lock:
                    self.errors.append({"phase": name, "error": type(error).__name__})

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(invoke, states))


def run_phases(runner, states, workers, *, fixture_preparation=False):
    runner.phase("login", states, runner.login, 6, synchronized=False)
    phase_workers = min(workers, 6) if fixture_preparation else workers
    if not runner.errors:
        runner.phase("start", states, runner.start, phase_workers, synchronized=not fixture_preparation)
    if not runner.errors:
        runner.phase("answer", states, runner.answer, phase_workers, synchronized=not fixture_preparation)


def fixture_runner(args, manifest):
    if manifest.get('capture_protocol'):
        from capture_protocol import CaptureProtocolMixin, CONTRACT
        if not args.fixture_preparation or manifest['capture_protocol'] != CONTRACT:
            raise SystemExit('Legacy assessment runner cannot consume a capture protocol fixture')
        class ProtocolPreparationRunner(CaptureProtocolMixin, Runner):
            pass
        runner = ProtocolPreparationRunner(args.base_url, manifest['task_id'], args.timeout)
        runner.protocol_course_id, runner.protocol_experiment_id = manifest['course_id'], manifest['experiment_id']
        return runner
    return Runner(args.base_url, manifest['task_id'], args.timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--capacity-report", required=True)
    parser.add_argument("--workers", type=int, default=300)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--fixture-preparation", action="store_true",
                        help="Prepare300 historical fixtures with at most6 HTTP students; no concurrent-capacity claim")
    parser.add_argument("--output", default="assessment-300-result.json")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    capacity = json.loads(Path(args.capacity_report).read_text(encoding="utf-8"))
    if capacity.get("source") != "server_cgroup_and_deployment_environment":
        raise SystemExit("capacity-report 不是服务器容器生成的容量报告。")
    if manifest.get('capture_protocol') and not args.fixture_preparation:
        raise SystemExit('Legacy assessment runner cannot consume a capture protocol fixture')
    from capture_protocol import evidence_files
    with evidence_files(args.manifest, args.output) as files:
        result = workload_result(args, manifest, capacity, files)
        json.dump(result, files['result'], ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


def workload_result(args, manifest, capacity, files):
    states = [
        StudentState(username, manifest["password"])
        for username in manifest["usernames"]
    ]
    if len(states) != 300:
        raise SystemExit("正式验收名单必须恰好包含 300 名学生。")
    if args.workers <= 0:
        raise SystemExit("--workers must be positive")
    if not args.fixture_preparation and args.workers < len(states):
        raise SystemExit("--workers 必须至少为 300，才能让同步起跑屏障正常工作。")
    runner = fixture_runner(args, manifest)
    files['runner'] = runner
    runner.attempt_output = files['attempts']
    started = time.time()
    run_phases(runner, states, args.workers, fixture_preparation=args.fixture_preparation)
    summary = {
        key: {
            "count": len(values),
            "p50_seconds": round(statistics.median(values), 3),
            "p95_seconds": percentile(values, 0.95),
            "max_seconds": round(max(values), 3),
        }
        for key, values in runner.metrics.items()
    }
    thresholds = {
        "login": 15,
        "start": 3,
        "enter_item": 1,
        "save_draft": 1,
        "event_batch": 2,
        "submit_item": 2,
        "finish": 3,
    }
    latency_pass = all(
        summary.get(key, {}).get("p95_seconds", float("inf")) <= limit
        for key, limit in thresholds.items()
    )
    result = {
        "mode": "historical_fixture_preparation" if args.fixture_preparation else "legacy_300_concurrent",
        "concurrent_http_students": min(args.workers, 6) if args.fixture_preparation else args.workers,
        "formal_capacity_accepted": False,
        "capacity": capacity,
        "manifest": {key: manifest[key] for key in ("run_id", "task_id", "student_count", "item_count") if key in manifest},
        "duration_seconds": round(time.time() - started, 3),
        "metrics": summary,
        "errors": runner.errors,
        "thresholds_p95_seconds": thresholds,
        "legacy_latency_passed": latency_pass,
        "historical_workflow_complete": (not runner.errors and len(states) == 300
            and manifest.get("item_count") == 20
            and summary.get("submit_item", {}).get("count") == 6000
            and summary.get("finish", {}).get("count") == 300),
        "passed": (
            not runner.errors
            and len(states) == 300
            and latency_pass
        ),
    }
    if args.fixture_preparation:
        result["passed"] = result["historical_workflow_complete"]
        if hasattr(runner, 'protocol_summary'):
            result['capture_protocol'] = runner.protocol_summary()
    files['complete'] = not runner.errors
    return result


if __name__ == "__main__":
    main()
