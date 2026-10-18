"""Sequential bounded v4 controller. Default: one 50-person qualification cell."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import capacity_teacher_v4 as contract
from run_teacher_capacity_v4 import atomic_json, monitor_valid


def select_cells(manifest, stage):
    if stage not in ("qualification", "baseline", "candidate-entry", "candidate-full-flow"):
        raise ValueError("Explicit bounded matrix stage required")
    return [c for c in contract.matrix(manifest) if c["stage"] == stage]


def call(args, *, timeout=600, allowed=(0,)):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode not in allowed:
        detail = (result.stderr or result.stdout).strip()[-4096:]
        raise RuntimeError(f"Owned command failed: {args[0]} exit={result.returncode}\n{detail}")
    return result


class Controller:
    def __init__(self, baseline_manifest, candidate_manifest, gates):
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise ValueError("Authenticated root on the school host required")
        self.manifests = {p: contract.validate_manifest(m) for p, m in
                          (("baseline", baseline_manifest), ("candidate", candidate_manifest))}
        self.root = Path(contract.ROOT)
        self.gates = gates
        self.path = self.root / "evidence/controller-v4.json"
        self.report = json.loads(self.path.read_text()) if self.path.exists() else {
            "project": contract.PROJECT, "cells": [], "qualification_passed": False,
            "formal_capacity_accepted": False, "configuration_changes": [], "state": "initialized"}
        if self.report["project"] != contract.PROJECT:
            raise ValueError("Controller report ownership differs")
        hashes = {p: contract.digest(m) for p, m in self.manifests.items()}
        if self.report.get("manifest_sha256", hashes) != hashes:
            raise ValueError("Source/configuration changed; cannot reuse earlier cells")
        self.report["manifest_sha256"] = hashes

    def compose(self, phase, *args, timeout=600):
        return call(["docker", "compose", "-p", contract.PROJECT, "-f",
                     str(self.root / f"private/compose-{phase}.json"), *args], timeout=timeout).stdout.strip()

    def manage(self, web, *args, timeout=600, allowed=(0,)):
        return call(["docker", "exec", "-e", "POSTGRES_HOST=db", "-e", "POSTGRES_POOL_ALIAS=",
                     "-e", "DATABASE_CONN_MAX_AGE=0", web, "python", "manage.py", *args],
                    timeout=timeout, allowed=allowed)

    def prepare_fixture(self, phase, cell_id, directory):
        return self.compose(phase, "run", "--rm", "--no-deps", "-T", "fixture-builder",
                            "prepare_teacher_capacity_v4_fixture", "--cell-id", cell_id,
                            "--directory", directory)

    @staticmethod
    def stop_owned_driver(name):
        inspected = call(["docker", "inspect", name], allowed=(0, 1))
        if inspected.returncode:
            return
        item = json.loads(inspected.stdout)[0]
        labels = item["Config"].get("Labels", {})
        if (labels.get("com.docker.compose.project") != contract.PROJECT
                or labels.get("com.docker.compose.service") != "driver"
                or item["Name"] != "/" + name or not name.startswith(contract.PROJECT + "-driver-")):
            raise ValueError("Refusing to stop a foreign driver")
        call(["docker", "stop", "--time", "15", name], timeout=25)

    def install(self, phase):
        gate = json.loads(Path(self.gates[phase]).read_text())
        if gate.get("passed") is not True or gate.get("source_archive_sha256") != self.manifests[phase]["source_archive_sha256"]:
            raise ValueError("Actual source correctness gate is missing; no container/load release")
        previous = self.report.get("active_phase")
        if previous and previous != phase:
            self.compose(previous, "stop")
        self.compose(phase, "up", "-d", "--wait", timeout=600)
        self.report["active_phase"] = phase
        web = self.compose(phase, "ps", "-q", "web")
        item = json.loads(call(["docker", "inspect", web]).stdout)[0]
        if item["Config"].get("Labels", {}).get("com.docker.compose.project") != contract.PROJECT:
            raise ValueError("Web identity is outside the new owned project")
        atomic_json(self.path, self.report)
        return web

    def cell(self, cell):
        phase, cell_id = cell["phase"], cell["cell_id"]
        manifest = self.manifests[phase]
        directory = self.root / "evidence/cells" / cell_id
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        inputs = self.root / "private/cells" / cell_id
        inputs.mkdir(parents=True, mode=0o700, exist_ok=False)
        web = self.install(phase)
        internal = "/app/media/teacher-capacity-v4/" + cell_id
        self.prepare_fixture(phase, cell_id, internal)
        for name in ("fixture.json", "teachers.json"):
            call(["docker", "cp", f"{web}:{internal}/{name}", str(inputs / name)])
            (inputs / name).chmod(0o600)
        atomic_json(inputs / "manifest.json", manifest)
        shutil.copyfile(self.gates[phase], inputs / "correctness-gate.json")
        (inputs / "correctness-gate.json").chmod(0o600)
        observer_dir = directory / "observer"
        observer_script = self.root / "source/candidate/server/scripts/load/observe_teacher_capacity_v4.py"
        observer = subprocess.Popen(["systemd-run", "--scope", "--quiet", "--unit=tv4-observer-" + cell_id,
            "--slice=ktteachercapacity-app.slice", sys.executable, str(observer_script),
            "--manifest", str(inputs / "manifest.json"), "--output", str(observer_dir), "--duration", "1800"],
            stdout=(directory / "observer-process.log").open("x"), stderr=subprocess.STDOUT)
        driver_parent = directory / "driver-data"
        driver_parent.mkdir(mode=0o700)
        driver_dir = driver_parent / "run"
        driver_exit = None
        try:
            deadline = time.monotonic() + 60
            while True:
                try:
                    ready = monitor_valid(json.loads((observer_dir / "monitor.json").read_text()), now=time.time())
                except (OSError, ValueError):
                    ready = False
                if ready:
                    break
                if observer.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Continuous observer did not become valid")
                time.sleep(.5)
            image = f"{contract.PROJECT}-driver:{manifest['source_archive_sha256'][:12]}"
            arguments = ["docker", "run", "--rm", "--name", contract.PROJECT + "-driver-" + cell_id,
                "--label", "com.docker.compose.project=" + contract.PROJECT,
                "--label", "com.docker.compose.service=driver", "--network", "host",
                "--cgroup-parent", "ktteachercapacity-driver.slice", "--cpus", "2", "--memory", "2g", "--memory-swap", "2g",
                "--mount", f"type=bind,src={inputs},dst=/inputs,readonly",
                "--mount", f"type=bind,src={observer_dir},dst=/monitor,readonly",
                "--mount", f"type=bind,src={driver_parent},dst=/outputs",
                "-e", "TEACHER_CAPACITY_PROJECT=" + contract.PROJECT, image,
                "--manifest", "/inputs/manifest.json", "--fixture", "/inputs/fixture.json", "--teachers", "/inputs/teachers.json",
                "--cell-id", cell_id, "--output", "/outputs/run", "--monitor-file", "/monitor/monitor.json",
                "--correctness-gate", "/inputs/correctness-gate.json", "--execute"]
            with (directory / "driver-process.log").open("x") as log:
                process = subprocess.Popen(arguments, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 1600
                while process.poll() is None:
                    try:
                        valid = monitor_valid(json.loads((observer_dir / "monitor.json").read_text()), now=time.time())
                    except (OSError, ValueError):
                        valid = False
                    if not valid or time.monotonic() > deadline:
                        self.stop_owned_driver(contract.PROJECT + "-driver-" + cell_id)
                        process.wait(30)
                        break
                    time.sleep(.5)
                driver_exit = process.wait()
            for name in ("plan.json", "records.jsonl", "browser-records.jsonl"):
                call(["docker", "cp", str(driver_dir / name), f"{web}:{internal}/{name}"])
            deadline = time.monotonic() + 300
            attempt = 0
            while True:
                attempt += 1
                verify = f"verification-{attempt}.json"
                args = ["verify_student_capacity_load", "--fixture", internal + "/fixture.json", "--plan", internal + "/plan.json",
                        "--records", internal + "/records.jsonl", "--browser-records", internal + "/browser-records.jsonl",
                        "--output", internal + "/" + verify, "--evidence-output", internal + f"/database-evidence-{attempt}.jsonl"]
                if cell["mode"] == "entry":
                    args.append("--skip-statistics")
                self.manage(web, *args, timeout=360)
                call(["docker", "cp", f"{web}:{internal}/{verify}", str(directory / verify)])
                verification = json.loads((directory / verify).read_text())
                if (cell["mode"] == "entry" or verification.get("stats_ready") is True
                        or verification.get("integrity_passed") is not True or time.monotonic() > deadline):
                    break
                time.sleep(5)
        finally:
            if observer_dir.exists():
                (observer_dir / "stop").touch()
            try:
                observer.wait(45)
            except subprocess.TimeoutExpired:
                observer.terminate();observer.wait(10)
        plan = json.loads((driver_dir / "plan.json").read_text())
        students = [json.loads(line) for name in ("records.jsonl", "browser-records.jsonl")
                    for line in (driver_dir / name).read_text().splitlines() if line.strip()]
        teachers = json.loads((driver_dir / "teacher-records.json").read_text())
        monitor = json.loads((observer_dir / "observer-final.json").read_text())
        health = [json.loads(line) for line in (driver_dir / "generator-health.jsonl").read_text().splitlines() if line.strip()]
        monitor["generator_valid"] = bool(health) and all(h.get("generator_valid") is True for h in health)
        verdict = contract.evaluate(cell, students, teachers, verification, monitor)
        summary = json.loads((driver_dir / "driver-summary.json").read_text())
        if driver_exit != 0 or not summary.get("thresholds_met") or summary.get("safety_stop"):
            verdict["passed"] = False;verdict["issues"].append("driver_or_student_threshold_failed")
        verdict.update(cell=cell, phase=phase, directory=str(directory),
                       measurement_valid="measurement_invalid" not in verdict["issues"] and "student_denominator_incomplete" not in verdict["issues"],
                       timings={"save": summary["save_scheduled"], "navigation": summary["navigation"], "submission": summary["submission"]},
                       binding=contract.digest({"source": manifest["source_archive_sha256"], "parameters": manifest["parameters"],
                           "resources": manifest["resources"], **{k: cell[k] for k in ("mode", "count", "window_seconds", "bootstrap", "round")}}))
        if cell["mode"] == "full-flow" and cell["teacher"] and cell["stage"] != "qualification":
            reference = next((r for r in self.report["cells"] if r.get("binding") == verdict["binding"] and not r["cell"]["teacher"]), None)
            comparison = contract.mixed_comparison(reference or {}, verdict)
            verdict["mixed_comparison"] = comparison
            verdict["passed"] &= comparison["passed"]
        atomic_json(directory / "cell-evaluation.json", verdict)
        return verdict

    def run(self, stage):
        if stage != "qualification" and not self.report["qualification_passed"]:
            raise ValueError("50-person correctness/measurement qualification must pass first")
        prerequisites = {"candidate-entry": ("baseline", 12), "candidate-full-flow": ("candidate-entry", 24)}
        if stage in prerequisites:
            previous, count = prerequisites[stage]
            rows = [r for r in self.report["cells"] if r["cell"]["stage"] == previous]
            if len(rows) != count or any(not r["measurement_valid"] or previous != "baseline" and not r["passed"] for r in rows):
                raise ValueError("Previous frozen matrix stage is incomplete or invalid")
        selected = select_cells(self.manifests["candidate"], stage)
        completed = {r["cell_id"] for r in self.report["cells"]}
        try:
            for cell in selected:
                if cell["cell_id"] in completed:
                    continue
                self.report.update(state="running", current_cell=cell)
                atomic_json(self.path, self.report)
                result = self.cell(cell)
                self.report["cells"].append(result)
                self.report.update(state="running", stage=stage, current_cell=None)
                if stage == "qualification":
                    self.report["qualification_passed"] = result["passed"]
                atomic_json(self.path, self.report)
                if not result["measurement_valid"] or stage != "baseline" and not result["passed"]:
                    raise RuntimeError("Cell failed; no further load released: " + cell["cell_id"])
            expected = contract.matrix(self.manifests["candidate"])
            self.report["formal_capacity_accepted"] = (len(self.report["cells"]) == len(expected)
                and all(r["measurement_valid"] and (r["cell"]["stage"] == "baseline" or r["passed"]) for r in self.report["cells"]))
            self.report["state"] = "stage_completed"
        except BaseException as error:
            self.report.update(state="stopped", error_type=type(error).__name__, no_further_load_released=True)
            raise
        finally:
            atomic_json(self.path, self.report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for phase in ("baseline", "candidate"):
        parser.add_argument("--" + phase + "-manifest", required=True)
        parser.add_argument("--" + phase + "-correctness-gate", required=True)
    parser.add_argument("--stage", choices=("qualification", "baseline", "candidate-entry", "candidate-full-flow"), default="qualification")
    args = parser.parse_args()
    manifests = [json.loads(Path(getattr(args, phase + "_manifest")).read_text()) for phase in ("baseline", "candidate")]
    Controller(*manifests, {p: getattr(args, p + "_correctness_gate") for p in ("baseline", "candidate")}).run(args.stage)
