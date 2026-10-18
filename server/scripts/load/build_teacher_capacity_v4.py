"""Freeze candidate source and b363+P4 baseline with separately built frontend assets."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

BASELINE = "b3636d9c12436588fc64912a96883e6140ad18f9"
MEASUREMENT_FILES = (
    "server/scripts/load/capacity_teacher_v4.py", "server/scripts/load/prepare_teacher_capacity_v4.py",
    "server/scripts/load/run_teacher_capacity_v4.py", "server/scripts/load/observe_teacher_capacity_v4.py",
    "server/scripts/load/control_teacher_capacity_v4.py", "server/scripts/load/build_teacher_capacity_v4.py",
    "server/scripts/load/test_teacher_capacity_v4.py",
    "server/apps/assessment/management/commands/prepare_teacher_capacity_v4_fixture.py",
    "server/apps/assessment/management/commands/seed_prepared_startup_load.py",
    "server/apps/assessment/test_prepared_startup_fixture.py",
    "client/e2e/teacher-capacity-v4-browser.mjs", "client/e2e/teacher-capacity-v4-browser.test.mjs",
    "deploy/Dockerfile.teacher-capacity-driver", "deploy/student-capacity-c4.json",
)


def allowed_source(name):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] not in {"server", "client", "deploy", "contracts"}:
        return name in {".dockerignore", "README.md"}
    banned = {".git", ".venv", ".auth", ".cache", "private", "node_modules", "__pycache__", "media", "staticfiles", "logs", "test-results", "playwright-report", "dist"}
    return not (banned & set(path.parts) or any(p.startswith((".env", ".tmp")) for p in path.parts)
                or path.name == "school-test.env" or path.suffix in {".pem", ".key", ".p12", ".sqlite3", ".pyc", ".log"})


def required_untracked(name):
    if name in MEASUREMENT_FILES:
        return True
    path = PurePosixPath(name)
    return (allowed_source(name) and (
        path.suffix == ".py" and name.startswith(("server/apps/", "server/config/", "server/scripts/", "server/tests/"))
        or path.suffix in {".ts", ".vue", ".mjs"} and name.startswith(("client/src/", "client/e2e/", "client/scripts/"))))


def command(args, *, cwd):
    return subprocess.check_output(args, cwd=cwd)


def archive(source, names, target):
    source, target = Path(source), Path(target)
    if target.exists():
        raise ValueError("Immutable source archive already exists")
    names = set(name for name in names if allowed_source(name))
    dist = source / "client/dist/spa-assets"
    if not dist.is_dir() or not any(dist.rglob("*.html")):
        raise ValueError("The corresponding frontend must be built before packaging")
    names.update(p.relative_to(source).as_posix() for p in dist.rglob("*") if p.is_file())
    with tarfile.open(target, "x:gz") as stream:
        for name in sorted(names):
            path = source / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Archive source absent or symlink: " + name)
            info = tarfile.TarInfo(name)
            content = path.read_bytes()
            info.size, info.mode, info.mtime = len(content), 0o755 if name.endswith(".sh") else 0o644, 0
            stream.addfile(info, io.BytesIO(content))
    return {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "files": len(names)}


def build_baseline_frontend(root, baseline, node_directory):
    node_directory = Path(node_directory)
    node = node_directory / ("node.exe" if os.name == "nt" else "node")
    if command([str(node), "--version"], cwd=root).decode().strip() != "v24.20.0":
        raise ValueError("The provisioned Node24.20.0 runtime is required")
    old, current = baseline / "client", root / "client"
    modules = old / "node_modules"
    if all((old / f).read_bytes() == (current / f).read_bytes() for f in ("package.json", "package-lock.json")):
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(modules), str(current / "node_modules")],
                           check=True, capture_output=True)
        else:
            modules.symlink_to(current / "node_modules", target_is_directory=True)
    else:
        npm = node_directory / "node_modules/npm/bin/npm-cli.js"
        subprocess.run([str(node), str(npm), "ci"], cwd=old, check=True)
    subprocess.run([str(node), str(modules / "vue-tsc/bin/vue-tsc.js"), "--noEmit"], cwd=old, check=True)
    subprocess.run([str(node), str(modules / "vite/bin/vite.js"), "build"], cwd=old, check=True)


def build(root, output, node_directory):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not output.is_relative_to(root) or output == root:
        raise ValueError("Artifacts must be in a dedicated directory inside this worktree")
    output.mkdir(mode=0o700, exist_ok=False)
    baseline = output / "baseline-source"
    baseline.mkdir()
    raw = command(["git", "archive", "--format=tar", BASELINE], cwd=root)
    names = []
    with tarfile.open(fileobj=io.BytesIO(raw)) as stream:
        for item in stream:
            if item.isfile() and allowed_source(item.name):
                target = baseline / item.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stream.extractfile(item).read())
                names.append(item.name)
    patch = root / "docs/reference/p4-observation.patch"
    if any(line.startswith("+++ b/") and line[6:] not in {
            "server/apps/api/middleware.py", "server/apps/api/test_capture_diagnostics.py", "server/apps/api/test_capture_timing.py"}
           for line in patch.read_text().splitlines()):
        raise ValueError("P4 patch contains a non-observation path")
    prefix = baseline.relative_to(root).as_posix()
    command(["git", "apply", "--check", "--directory=" + prefix, str(patch)], cwd=root)
    command(["git", "apply", "--directory=" + prefix, str(patch)], cwd=root)
    for name in MEASUREMENT_FILES:
        target = baseline / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
        names.append(name)
    # Only expose existing browser helpers; preserve all historical logic.
    helper = baseline / "client/e2e/student-capacity-retest-browser.mjs"
    text = helper.read_text(encoding="utf-8")
    for name in ("cleanupRuntime", "login", "startup", "driveSchedule"):
        text = text.replace(f"\nasync function {name}(", f"\nexport async function {name}(")
    helper.write_text(text, encoding="utf-8")
    build_baseline_frontend(root, baseline, node_directory)
    candidate_names = command(["git", "ls-files", "--cached", "-z"], cwd=root).decode().split("\0")
    candidate_names.extend(name for name in command(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root).decode().split("\0")
                           if required_untracked(name))
    report = {"baseline_commit": BASELINE, "p4_patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
              "baseline": archive(baseline, names, output / "baseline.tar.gz"),
              "candidate": archive(root, candidate_names, output / "candidate.tar.gz"),
              "measurement_files": list(MEASUREMENT_FILES), "production_operations": False}
    (output / "source-packages.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--node-directory", default=r"C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64")
    args = parser.parse_args()
    print(json.dumps(build(args.checkout, args.output, args.node_directory), indent=2))
