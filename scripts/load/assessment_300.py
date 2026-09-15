#!/usr/bin/env python3
"""Run the formal 300-student HTTP acceptance test with Python stdlib only."""

import argparse
import concurrent.futures
import http.cookiejar
import json
import math
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
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
    def __init__(self, base_url, task_id, timeout):
        self.base_url = base_url.rstrip("/")
        self.task_id = task_id
        self.timeout = timeout
        self.metrics = {}
        self.errors = []
        self.lock = threading.Lock()

    def _csrf(self, state):
        return next(
            (cookie.value for cookie in state.cookies if cookie.name == "csrftoken"),
            "",
        )

    def request(self, state, path, *, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if method != "GET":
            headers["X-CSRFToken"] = self._csrf(state)
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        started = time.perf_counter()
        try:
            with state.opener.open(request, timeout=self.timeout) as response:
                payload = response.read()
                content_type = response.headers.get("Content-Type", "")
                parsed = json.loads(payload) if "json" in content_type else payload
                return response.status, parsed, time.perf_counter() - started
        except urllib.error.HTTPError as error:
            payload = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code} {path}: {payload[:300]}") from error

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

    def answer(self, state):
        client_instance_id = str(uuid.uuid4())
        sequence = 0
        next_assignment = state.assignment_ids[0]
        visited = set()
        finish_url = ""
        while next_assignment not in visited:
            visited.add(next_assignment)
            status, payload, seconds = self.request(
                state,
                f"/api/v1/student/sessions/{state.session_id}/questions/{next_assignment}/",
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
        status, _, seconds = self.request(
            state, finish_url, method="POST", body={}
        )
        self.record("finish", seconds)
        if status != 200:
            raise RuntimeError(f"finish returned {status}")

    def phase(self, name, states, operation, workers):
        barrier = threading.Barrier(len(states))

        def invoke(state):
            try:
                barrier.wait(timeout=30)
                operation(state)
            except Exception as error:
                with self.lock:
                    self.errors.append({"phase": name, "student": state.username, "error": str(error)})

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(invoke, states))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--capacity-report", required=True)
    parser.add_argument("--workers", type=int, default=300)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--output", default="assessment-300-result.json")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    capacity = json.loads(Path(args.capacity_report).read_text(encoding="utf-8"))
    if capacity.get("source") != "server_cgroup_and_deployment_environment":
        raise SystemExit("capacity-report 不是服务器容器生成的容量报告。")
    states = [
        StudentState(username, manifest["password"])
        for username in manifest["usernames"]
    ]
    if len(states) != 300:
        raise SystemExit("正式验收名单必须恰好包含 300 名学生。")
    if args.workers < len(states):
        raise SystemExit("--workers 必须至少为 300，才能让同步起跑屏障正常工作。")
    runner = Runner(args.base_url, manifest["task_id"], args.timeout)
    started = time.time()
    runner.phase("login", states, runner.login, args.workers)
    runner.phase("start", states, runner.start, args.workers)
    runner.phase("answer", states, runner.answer, args.workers)
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
        "capacity": capacity,
        "manifest": {key: value for key, value in manifest.items() if key != "password"},
        "duration_seconds": round(time.time() - started, 3),
        "metrics": summary,
        "errors": runner.errors,
        "thresholds_p95_seconds": thresholds,
        "passed": (
            not runner.errors
            and len(states) == 300
            and capacity.get("eligible_for_300_user_acceptance") is True
            and latency_pass
        ),
    }
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
