"""Render/build only the fresh, explicitly owned school teacher capacity project."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import shutil
import subprocess
import tarfile

from capacity_teacher_v4 import PROJECT, ROOT, PORT, ORIGIN, validate_manifest


def call(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=kwargs.pop("timeout", 600), **kwargs)


def private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as stream:
        stream.write(value)


def rebind_compose(config, manifest, *, phase):
    validate_manifest(manifest)
    if phase not in ("baseline", "candidate"):
        raise ValueError("Explicit baseline/candidate phase required")
    result = copy.deepcopy(config)
    result["name"] = PROJECT
    for kind in ("volumes", "networks"):
        for alias, resource in result.get(kind, {}).items():
            if resource.get("external"):
                raise ValueError("No external or old-project resources permitted")
            resource["name"] = f"{PROJECT}_{phase}_{alias}"
    active = {role: service for role, service in result["services"].items() if not service.get("profiles")}
    result["services"] = active
    source = Path(ROOT) / "source" / phase
    parameters = manifest["parameters"]
    for role, service in active.items():
        service.pop("container_name", None)
        service.pop("build", None)
        service["cgroup_parent"] = "ktteachercapacity-app.slice"
        if role == "nginx":
            service["image"] = f"{PROJECT}-frontend:{phase}-{manifest['source_archive_sha256'][:12]}"
            service["ports"] = [{"host_ip": "127.0.0.1", "published": str(PORT), "target": 80, "protocol": "tcp"}]
        elif role not in ("db", "pgbouncer", "redis-admission", "redis-session"):
            service["image"] = f"{PROJECT}-backend:{phase}-{manifest['source_archive_sha256'][:12]}"
            service.setdefault("environment", {}).update(
                POSTGRES_DB="kt_e2e", SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED="1",
                SCHOOL_CAPTURE_DIAGNOSTICS_MODE="light", ASSESSMENT_REQUEST_TIMING_ENABLED="1",
                DJANGO_CSRF_TRUSTED_ORIGINS=f"{ORIGIN},http://localhost:{PORT}",
                RUN_SCHEDULER_EXPORT_JOBS="0", SCHOOL_REDIS_SESSION_ENABLED="0",
                TEACHER_CAPACITY_PROJECT=PROJECT)
        for key, value in parameters.items():
            if key.startswith(f"resource.{role}."):
                field = key.split(".", 2)[2]
                service[field] = int(value) if field in ("mem_limit", "cpu_shares") else value
        service["cgroup_parent"] = "ktteachercapacity-app.slice"
        for mount in service.get("volumes", []):
            if mount["type"] == "bind" and not Path(mount["source"]).resolve().is_relative_to(source.resolve()):
                raise ValueError("Bind mount outside this phase's frozen source")
            if mount["type"] == "volume" and mount["source"] not in result.get("volumes", {}):
                raise ValueError("Undeclared volume")
        for dependency, policy in list(service.get("depends_on", {}).items()):
            if dependency not in active:
                if policy.get("required", True):
                    raise ValueError("Required service missing: " + dependency)
                del service["depends_on"][dependency]
    if phase == "candidate":
        worker = copy.deepcopy(active["web"])
        worker.update(command=["python", "manage.py", "run_research_artifact_cleanups"],
                      mem_limit=256 * 1024**2, cpu_shares=128, deploy={"replicas": 1})
        worker.pop("healthcheck", None)
        worker.pop("ports", None)
        worker["environment"].update(POSTGRES_HOST="db", POSTGRES_POOL_ALIAS="", DATABASE_CONN_MAX_AGE="0",
                                     DATABASE_APPLICATION_NAME="kt-teacher-cleanup")
        active["artifact-cleanup-worker"] = worker
    fixture = copy.deepcopy(active["web"])
    fixture.update(entrypoint=["python", "manage.py"], command=["check"],
                   profiles=["fixture"], restart="no", mem_limit=2 * 1024**3,
                   cpus=2, cpu_shares=128, deploy={"replicas": 1})
    fixture.pop("healthcheck", None)
    fixture.pop("ports", None)
    fixture["environment"].update(POSTGRES_HOST="db", POSTGRES_POOL_ALIAS="", DATABASE_CONN_MAX_AGE="0",
                                   DATABASE_APPLICATION_NAME="kt-teacher-fixture")
    active["fixture-builder"] = fixture
    return result


def require_fresh_project(manifest):
    validate_manifest(manifest)
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ValueError("Run inside the authenticated school root execution context")
    if call(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT]).strip():
        raise ValueError("The new project already has containers; refusing reuse")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    names = call(["docker", "volume", "ls", "--format", "{{.Name}}"])
    if any(name.startswith(PROJECT + "_") for name in names.splitlines()):
        raise ValueError("The new project already has volumes; refusing old/fresh-state ambiguity")


def create_owned_slices(manifest):
    limits = manifest["resources"]
    definitions = {
        "ktteachercapacity.slice": (limits["cpu_quota_percent"], limits["memory_bytes"]),
        "ktteachercapacity-app.slice": (1000, limits["app_memory_bytes"]),
        "ktteachercapacity-driver.slice": (200, limits["driver_memory_bytes"]),
    }
    for name, (cpu, memory) in definitions.items():
        target = Path("/run/systemd/system") / name
        text = f"[Unit]\nDescription=Owned teacher capacity v4 resources\n[Slice]\nCPUQuota={cpu}%\nMemoryMax={memory}\nMemorySwapMax=0\n"
        if target.exists():
            if target.read_text() != text:
                raise ValueError("Owned slice exists with different limits")
        else:
            private(target, text)
    call(["systemctl", "daemon-reload"])
    call(["systemctl", "start", *definitions])


def prepare(archive_path, manifest, *, phase, build=False):
    require_fresh_project(manifest)
    archive = Path(archive_path).resolve()
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["source_archive_sha256"]:
        raise ValueError("Frozen source archive digest differs")
    root, source = Path(ROOT), Path(ROOT) / "source" / phase
    source.mkdir(parents=True, mode=0o700)
    with tarfile.open(archive) as stream:
        if any(Path(m.name).is_absolute() or ".." in Path(m.name).parts or m.issym() or m.islnk()
               for m in stream.getmembers()):
            raise ValueError("Unsafe source archive")
        for member in stream.getmembers():
            target = source / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError("Only source files/directories may be extracted")
    env = {key: value for key, value in manifest["parameters"].items() if not key.startswith("resource.")}
    env.update(POSTGRES_DB="kt_e2e", POSTGRES_USER="kt_user", POSTGRES_PASSWORD=secrets.token_urlsafe(32),
               DJANGO_SECRET_KEY=secrets.token_urlsafe(64), POSTGRES_HOST="pgbouncer", POSTGRES_PORT="5432",
               SCHOOL_RUNTIME_ISOLATION_ENABLED="1", SCHOOL_REDIS_SESSION_ENABLED="0",
               DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1", CANONICAL_PUBLIC_ORIGIN="", DJANGO_DEBUG="0",
               DJANGO_CSRF_TRUSTED_ORIGINS=f"{ORIGIN},http://localhost:{PORT}",
               DJANGO_SECURE_SSL_REDIRECT="0", DJANGO_SESSION_COOKIE_SECURE="0", DJANGO_CSRF_COOKIE_SECURE="0",
               SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED="1", SCHOOL_CAPTURE_DIAGNOSTICS_MODE="light",
               ASSESSMENT_TRANSITION_ENABLED="1", ASSESSMENT_TRANSITION_COURSE_IDS="*", CLOSE_BUNDLE_ENABLED="1",
               STUDENT_COURSE_NAVIGATION_ENABLED="1", STUDENT_COURSE_NAVIGATION_USER_IDS="*",
               RUN_SCHEDULER_EXPORT_JOBS="0")
    text = "".join(key + "=" + json.dumps(str(value)) + "\n" for key, value in env.items())
    private(source / "server/.env", text)
    private(source / "deploy/school-test.env", text)
    chosen = os.environ | env | {"SCHOOL_TEST_POSTGRES_USER": env["POSTGRES_USER"],
                                 "SCHOOL_TEST_POSTGRES_PASSWORD": env["POSTGRES_PASSWORD"],
                                 "STUDENT_CONCURRENCY_HTTP_PORT": str(PORT)}
    args = ["docker", "compose", "-p", PROJECT]
    for name in ("docker-compose.yml", "docker-compose.school.yml", "docker-compose.school-test.yml",
                 "docker-compose.student-concurrency.yml", "docker-compose.student-entry-sharing.yml",
                 "docker-compose.student-capacity.yml"):
        args.extend(["-f", str(source / "deploy" / name)])
    rendered = rebind_compose(json.loads(call(args + ["config", "--format", "json"], env=chosen)), manifest, phase=phase)
    path = root / "private" / f"compose-{phase}.json"
    private(path, json.dumps(rendered, indent=2))
    private(root / "private" / f"manifest-{phase}.json", json.dumps(manifest, indent=2))
    call(["docker", "compose", "-p", PROJECT, "-f", str(path), "config", "--quiet"])
    if build:
        for role, dockerfile, target in (("backend", "deploy/Dockerfile.school", "web"),
                                          ("frontend", "client/Dockerfile", "production")):
            image = f"{PROJECT}-{role}:{phase}-{manifest['source_archive_sha256'][:12]}"
            call(["docker", "build", "-f", str(source / dockerfile), "--target", target, "-t", image, str(source)], timeout=1800)
        call(["docker", "build", "-f", str(source / "deploy/Dockerfile.teacher-capacity-driver"),
              "-t", f"{PROJECT}-driver:{manifest['source_archive_sha256'][:12]}", str(source)], timeout=2400)
    private(root / "evidence" / f"prepared-{phase}.json", json.dumps({
        "project": PROJECT, "phase": phase, "source_sha256": manifest["source_archive_sha256"],
        "compose_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "fresh_only": True,
        "services_started": False, "build_requested": build}, indent=2))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--phase", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--create-owned-slices", action="store_true")
    args = parser.parse_args()
    manifest = validate_manifest(json.loads(Path(args.manifest).read_text(encoding="utf-8-sig")))
    path = prepare(args.archive, manifest, phase=args.phase, build=args.build)
    if args.create_owned_slices:
        create_owned_slices(manifest)
    print(json.dumps({"project": PROJECT, "compose": str(path), "started": False}))


if __name__ == "__main__":
    main()
