"""Independent read-only Docker/cgroup/PostgreSQL observer for the new v4 project."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import capacity_teacher_v4 as contract
import collect_student_capacity_logs as logs
import observe_school_runtime as existing
from run_teacher_capacity_v4 import atomic_json


def call(args):
    return subprocess.check_output(args, text=True, timeout=5)


def containers():
    ids = call(["docker", "ps", "-aq", "--no-trunc"]).split()
    return json.loads(call(["docker", "inspect", *ids])) if ids else []


def foreign_signature(items):
    return sorted((r["Id"], r["Image"], r["State"]["Running"], r["State"]["StartedAt"], r.get("RestartCount", 0))
                  for r in items if r["Config"].get("Labels", {}).get("com.docker.compose.project") != contract.PROJECT)


def observe(manifest, output, *, duration=1800):
    contract.validate_manifest(manifest)
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ValueError("Root read-only Docker/cgroup observation required")
    output = Path(output).resolve()
    if not output.is_relative_to(Path(contract.ROOT) / "evidence"):
        raise ValueError("Observer evidence must stay in the new project's evidence directory")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    initial = containers()
    foreign = foreign_signature(initial)
    logs.ROOT, logs.PROJECT = Path(contract.ROOT), contract.PROJECT
    logs.ROLES = logs.ROLES | {"db", "artifact-cleanup-worker", "driver"}
    collector = logs.Collector(output / "logs", output / "stop", duration)
    collector_errors = []
    def collect():
        try:
            collector.run()
        except BaseException as error:
            collector_errors.append(type(error).__name__)
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    start = time.monotonic()
    result = {"project": contract.PROJECT, "monitor_ok": False, "updated_at": time.time()}
    with (output / "samples.jsonl").open("x", encoding="utf-8") as stream:
        os.chmod(output / "samples.jsonl", 0o600)
        try:
            while time.monotonic() - start < duration and not (output / "stop").exists():
                stamp = time.monotonic()
                items = containers()
                own = [r for r in items if r["Config"].get("Labels", {}).get("com.docker.compose.project") == contract.PROJECT
                       and r["State"]["Running"]]
                databases = [r for r in own if r["Config"]["Labels"].get("com.docker.compose.service") == "db"]
                if len(databases) != 1:
                    raise ValueError("Exactly one new isolated database required")
                kernels = []
                for item in own:
                    group = existing.cgroup_path(item)
                    if "ktteachercapacity.slice" not in str(group):
                        raise ValueError("Owned container escaped the dedicated resource envelope")
                    kernels.append({"container_id": item["Id"], "role": item["Config"]["Labels"].get("com.docker.compose.service"),
                                    **existing.cgroup_metrics(group)})
                    kernel = kernels[-1]
                    driver = kernel["role"] == "driver"
                    expected_cpu, expected_memory = ((2, manifest["resources"]["driver_memory_bytes"]) if driver else
                                                     (10, manifest["resources"]["app_memory_bytes"]))
                    if (kernel["effective_cpu_quota_cores"] is None or kernel["effective_cpu_quota_cores"] > expected_cpu
                            or kernel["effective_memory_max_bytes"] is None or kernel["effective_memory_max_bytes"] > expected_memory):
                        raise ValueError("Actual kernel resources exceed the frozen dedicated envelope")
                database = databases[0]["Id"]
                metrics = {}
                for name, query in (("database_waits", existing.DATABASE_WAIT_SQL),
                                    ("database_wal", existing.DATABASE_WAL_SQL), ("backlog", existing.BACKLOG_SQL)):
                    metrics[name] = json.loads(call(["docker", "exec", database, "sh", "-c",
                        'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"', "v4-readonly-observer", query]))
                result = {"project": contract.PROJECT, "updated_at": time.time(),
                          "non_project_unchanged": foreign_signature(items) == foreign,
                          "oom": any(k["memory_events"].get("oom_kill", 0) > 0 or k["memory_events"].get("oom", 0) > 0 for k in kernels),
                          "memory_available_bytes": existing.memory_available_bytes(),
                          "sample_seconds": time.monotonic() - stamp, "resources": kernels, **metrics}
                log_state = collector.report
                covered = {(r["container_id"], r["started_at"]) for r in log_state["streams"] if r["state"] == "following"}
                required = {(r["Id"], r["State"]["StartedAt"]) for r in own
                            if r["Config"]["Labels"].get("com.docker.compose.service") in logs.ROLES - {"driver"}}
                result["log_coverage_ok"] = bool(required) and required <= covered and not collector_errors and not log_state["coverage_gaps"]
                result["monitor_ok"] = (result["non_project_unchanged"] and not result["oom"]
                    and result["memory_available_bytes"] >= 2 * 1024**3
                    and result["sample_seconds"] < 30 and result["log_coverage_ok"])
                stream.write(json.dumps(result) + "\n");stream.flush()
                atomic_json(output / "monitor.json", result)
                if result["oom"] or not result["non_project_unchanged"]:
                    break
                time.sleep(max(0, 2 - (time.monotonic() - stamp)))
        except BaseException as error:
            result.update(monitor_ok=False, error_type=type(error).__name__, updated_at=time.time())
            atomic_json(output / "monitor.json", result)
            raise
        finally:
            (output / "stop").touch()
            thread.join(15)
            result.update(finished_at=time.time(), collector_complete=collector.report.get("state") == "completed")
            atomic_json(output / "observer-final.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.duration <= 43200:
        parser.error("Duration must be bounded by twelve hours")
    observe(json.loads(Path(args.manifest).read_text(encoding="utf-8-sig")), args.output, duration=args.duration)


if __name__ == "__main__":
    main()
