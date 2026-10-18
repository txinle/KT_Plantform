"""Run one frozen v4 cell in its school driver container, or emit the frozen matrix.

Execution records evidence only. Independent PostgreSQL reconciliation and the
matching continuous host observer are required before any capacity acceptance.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import capacity_teacher_v4 as contract
import student_capacity_protocol_v3 as protocol
from student_concurrency_protocol import HttpClient, ProtocolFailure, private_output


def monitor_valid(row, *, now):
    return (row.get("project") == contract.PROJECT and isinstance(row.get("updated_at"), (int, float))
            and 0 <= now - row["updated_at"] <= 30 and row.get("monitor_ok") is True
            and row.get("non_project_unchanged") is True and row.get("oom") is False
            and row.get("memory_available_bytes", 0) >= 2 * 1024**3)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
    temporary.chmod(0o600)
    os.replace(temporary, path)


class GuardedClient(HttpClient):
    def __init__(self, base_url, stop):
        super().__init__(base_url)
        self.stop = stop

    def _request(self, *args, **kwargs):
        if self.stop.is_set():
            raise ProtocolFailure("v4_safety_stop")
        return super()._request(*args, **kwargs)


class Actor(protocol.Actor):
    def prepare(self, preparation_login_gate):
        if self.plan["bootstrap"] == "warm":
            with preparation_login_gate:
                self.csrf()
                self.record["anonymous_csrf_prepared"] = True

    def login(self):
        cold = not self.record.get("anonymous_csrf_prepared")
        dispatch_skew = self.record.get("release_skew_ms", 0)
        if cold:
            began = time.monotonic()
            self.csrf()
            self.record.update(anonymous_csrf_prepared=True,
                               cold_bootstrap_ms=(time.monotonic() - began) * 1000)
        try:
            return super().login()
        finally:
            if cold:
                self.record["cold_first_post_delay_ms"] = max(0, self.record.get("release_skew_ms", dispatch_skew) - dispatch_skew)
                self.record["release_skew_ms"] = dispatch_skew


def execute(manifest, cell, fixture_path, teachers_path, output, monitor_file, correctness_gate):
    contract.validate_manifest(manifest)
    if os.getenv("TEACHER_CAPACITY_PROJECT") != contract.PROJECT or sys.platform != "linux" or not Path("/.dockerenv").exists():
        raise ValueError("Pressure may run only inside the owned school Linux driver container")
    import resource
    gate = json.loads(Path(correctness_gate).read_text())
    if gate.get("passed") is not True or gate.get("source_archive_sha256") != manifest["source_archive_sha256"]:
        raise ValueError("The actual frozen source must pass correctness before load")
    raw = Path(fixture_path).read_bytes()
    fixture = json.loads(raw)
    import hashlib
    plan = contract.build_plan(manifest, cell, fixture, hashlib.sha256(raw).hexdigest())
    output = Path(output)
    output.mkdir(mode=0o700, exist_ok=False)
    atomic_json(output / "plan.json", plan)
    atomic_json(output / "manifest.json", manifest)
    stop, released = threading.Event(), threading.Event()
    readiness, actors, state = {}, {}, {}
    records = []
    semaphore = threading.BoundedSemaphore(4)
    planned = [r for r in plan["roster"] if r["driver"] == "protocol"]
    monitor_path = Path(monitor_file)

    def monitoring():
        expected = time.monotonic()
        with private_output(output / "generator-health.jsonl") as stream:
            while not stop.wait(max(0, expected - time.monotonic())):
                now = time.monotonic()
                try:
                    observed = json.loads(monitor_path.read_text())
                    if not monitor_valid(observed, now=time.time()):
                        raise ValueError("host_observation_invalid")
                    late = max(0, (now - expected) * 1000)
                    row = {"at": time.time(), "heartbeat_lateness_ms": late,
                           "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                           "cpu_seconds": time.process_time(), "generator_valid": late <= 1000}
                    if late > 1000:
                        raise ValueError("generator_heartbeat_late")
                    stream.write(json.dumps(row) + "\n");stream.flush()
                except (OSError, ValueError, TypeError):
                    state["safety_stop"] = True
                    stop.set();released.set()
                    return
                expected += 1

    def worker(row):
        actor = Actor(next(a for a in fixture["accounts"] if a["account_id"] == row["account_id"]),
                      row, plan, GuardedClient(contract.ORIGIN, stop))
        actors[row["account_id"]] = actor
        try:
            actor.prepare(semaphore)
            readiness[row["account_id"]] = True
            if not released.wait(300) or stop.is_set():
                raise ProtocolFailure("v4_release_cancelled")
            actor.run(state["release_monotonic"])
        except Exception as error:
            actor.record.update(status="failed", error_code=str(error) if isinstance(error, ProtocolFailure) else type(error).__name__)
        finally:
            readiness.setdefault(row["account_id"], False)
        record = actor.finish_record()
        record.update(same_DUT=True, capacity_version=contract.VERSION)
        return record

    if not monitor_valid(json.loads(monitor_path.read_text()), now=time.time()):
        raise ValueError("Fresh complete host observation required before browser/HTTP preparation")
    monitor_thread = threading.Thread(target=monitoring, daemon=True)
    monitor_thread.start()
    node = subprocess.Popen(["node", str(Path(__file__).resolve().parents[3] / "client/e2e/teacher-capacity-v4-browser.mjs"),
        "--manifest", str(output / "manifest.json"), "--plan", str(output / "plan.json"),
        "--fixture", str(fixture_path), "--teachers", str(teachers_path), "--output", str(output),
        "--ready-file", str(output / "browser-ready.json"), "--release-file", str(output / "release.json")],
        stdout=(output / "browser-process.log").open("x"), stderr=subprocess.STDOUT)
    def abort(code):
        stop.set();released.set()
        raise ProtocolFailure(code)
    try:
        with ThreadPoolExecutor(max_workers=len(planned), thread_name_prefix="teacher-v4") as pool, private_output(output / "records.jsonl") as stream:
            futures = [pool.submit(worker, row) for row in planned]
            deadline = time.monotonic() + 300
            while len(readiness) < len(planned) or not (output / "browser-ready.json").exists():
                if stop.is_set() or node.poll() is not None or time.monotonic() > deadline:
                    abort("v4_preparation_incomplete")
                time.sleep(.05)
            if not all(readiness.values()):
                abort("v4_preparation_failed")
            browser_ready = json.loads((output / "browser-ready.json").read_text())
            if browser_ready.get("ready") is not True or browser_ready.get("plan_id") != plan["plan_id"]:
                abort("v4_browser_plan_mismatch")
            state["release_monotonic"] = time.monotonic() + 3
            atomic_json(output / "release.json", {"release_monotonic": state["release_monotonic"], "release_wall_time": time.time() + 3})
            released.set()
            for future in as_completed(futures):
                record = future.result();records.append(record)
                stream.write(json.dumps(record, ensure_ascii=False) + "\n");stream.flush()
            until = time.monotonic() + 360
            while not (output / "browser-students-finished.json").exists():
                if stop.is_set() or node.poll() is not None or time.monotonic() > until:
                    abort("v4_browser_completion_incomplete")
                time.sleep(.1)
            atomic_json(output / "student-pressure-finished.json", {"at": time.time(), "plan_id": plan["plan_id"]})
            until = time.monotonic() + 660
            while node.poll() is None:
                if stop.is_set() or time.monotonic() > until:
                    abort("v4_browser_or_monitor_failed")
                time.sleep(.25)
    finally:
        stop.set();released.set();monitor_thread.join(2)
        if node.poll() is None:
            node.terminate()
            try:
                node.wait(5)
            except subprocess.TimeoutExpired:
                node.kill();node.wait()
        browser_path = output / "browser-records.jsonl"
        browsers = [json.loads(line) for line in browser_path.read_text().splitlines() if line.strip()] if browser_path.exists() else []
        report = protocol.summarize(plan, records + browsers)
        report.update(capacity_version=contract.VERSION, capacity_accepted=False,
                      safety_stop=state.get("safety_stop", False), same_DUT=True,
                      independent_database_reconciliation_required=True)
        atomic_json(output / "driver-summary.json", report)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest")
    parser.add_argument("--source-sha256")
    parser.add_argument("--emit-manifest")
    parser.add_argument("--emit-matrix")
    for name in ("fixture", "teachers", "cell-id", "output", "monitor-file", "correctness-gate"):
        parser.add_argument("--" + name)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--verification-file")
    parser.add_argument("--observer-final")
    args = parser.parse_args()
    manifest = (json.loads(Path(args.manifest).read_text(encoding="utf-8-sig")) if args.manifest else
                contract.default_manifest(args.source_sha256))
    contract.validate_manifest(manifest)
    if args.emit_manifest:
        with private_output(args.emit_manifest) as stream:
            json.dump(manifest, stream, indent=2)
    if args.emit_matrix:
        with private_output(args.emit_matrix) as stream:
            json.dump(contract.matrix(manifest), stream, indent=2)
    if args.execute:
        for name in ("fixture", "teachers", "cell_id", "output", "monitor_file", "correctness_gate"):
            if not getattr(args, name):
                parser.error("--" + name.replace("_", "-") + " required for execution")
        cell = next((c for c in contract.matrix(manifest) if c["cell_id"] == args.cell_id), None)
        if cell is None:
            parser.error("Unknown frozen cell ID")
        execute(manifest, cell, args.fixture, args.teachers, args.output, args.monitor_file, args.correctness_gate)
    if args.evaluate:
        if not args.output or not args.verification_file or not args.observer_final:
            parser.error("Evaluation requires output, verification-file and observer-final")
        output = Path(args.output)
        plan = json.loads((output / "plan.json").read_text())
        if plan.get("manifest_sha256") != contract.digest(manifest):
            raise ValueError("Evaluation manifest differs from the actual frozen driver plan")
        students = [json.loads(line) for name in ("records.jsonl", "browser-records.jsonl")
                    for line in (output / name).read_text().splitlines() if line.strip()]
        teachers = json.loads((output / "teacher-records.json").read_text())
        observer = json.loads(Path(args.observer_final).read_text())
        health = [json.loads(line) for line in (output / "generator-health.jsonl").read_text().splitlines() if line.strip()]
        observer["generator_valid"] = bool(health) and all(r.get("generator_valid") is True for r in health)
        report = contract.evaluate(plan["cell"], students, teachers,
                                   json.loads(Path(args.verification_file).read_text()), observer)
        report.update(capacity_accepted=False, scope="One cell only; the full frozen matrix and paired mixed comparison remain required")
        atomic_json(output / "cell-evaluation.json", report)
        print(json.dumps({"cell_id": report["cell_id"], "passed": report["passed"], "issues": report["issues"]}))


if __name__ == "__main__":
    main()
