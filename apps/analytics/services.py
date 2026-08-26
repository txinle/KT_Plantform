import hashlib
import json
import csv
import io
import os
import subprocess
import zipfile
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils import timezone

from apps.assessment.models import AssessmentEvent, Attempt, ExperimentSession, EventSchemaArtifact

from .models import ExportJob, ExportSnapshot


def create_export_snapshot(*, course, experiment=None, created_by):
    versions = []
    if experiment is not None:
        versions = list(experiment.versions.order_by("version_number").values_list("pk", "version_number"))
    return ExportSnapshot.objects.create(
        course=course,
        experiment=experiment,
        created_by=created_by,
        cutoff_at=timezone.now(),
        selected_versions=[{"id": pk, "version": version} for pk, version in versions],
    )


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_export_manifest(snapshot, *, selected_tables):
    schemas = {
        f"{row.event_type}:v{row.event_version}": row.schema_sha256
        for row in EventSchemaArtifact.objects.all()
    }
    return {
        "manifest_version": 1,
        "export_snapshot_id": str(snapshot.snapshot_id),
        "course_id": snapshot.course_id,
        "experiment_id": snapshot.experiment_id,
        "cutoff_at": snapshot.cutoff_at.isoformat(),
        "database_snapshot_strategy": snapshot.database_snapshot_strategy,
        "selected_tables": list(selected_tables),
        "event_schema_hashes": schemas,
        "application_git_commit_sha": _git_commit(),
    }


def manifest_bytes(manifest):
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def claim_export_job(*, worker_id, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        job = (
            ExportJob.objects.select_for_update()
            .filter(status=ExportJob.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if not job:
            return None
        job.status = ExportJob.Status.RUNNING
        job.locked_by = worker_id
        job.started_at = now
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=90)
        job.progress_stage = "snapshot"
        job.save(update_fields=["status", "locked_by", "started_at", "heartbeat_at", "lease_expires_at", "progress_stage"])
        return job


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def run_export_job(job):
    """Build a deterministic, pseudonymized ZIP from the job's frozen cutoff."""
    snapshot = job.snapshot
    root = os.path.join(settings.MEDIA_ROOT, "exports", str(job.job_id))
    os.makedirs(root, exist_ok=True)
    zip_path = os.path.join(root, f"{job.package}-{job.job_id}.zip")
    experiment_filter = {"session__version__experiment_id": job.experiment_id} if job.experiment_id else {"session__version__experiment__course_id": job.course_id}
    attempts = Attempt.objects.filter(**experiment_filter, session__started_at__lte=snapshot.cutoff_at).select_related("session", "assignment__snapshot")
    sessions = ExperimentSession.objects.filter(**{"version__experiment_id": job.experiment_id} if job.experiment_id else {"version__experiment__course_id": job.course_id}, started_at__lte=snapshot.cutoff_at).select_related("student", "version")
    events = AssessmentEvent.objects.filter(**({"session__version__experiment_id": job.experiment_id} if job.experiment_id else {"session__version__experiment__course_id": job.course_id}), server_received_at__lte=snapshot.cutoff_at).order_by("session_id", "server_ingest_sequence_no")
    manifest = build_export_manifest(snapshot, selected_tables=["subjects", "sessions", "attempts", "assessment_events"])
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("00_manifest/manifest.json", manifest_bytes(manifest))
        archive.writestr("01_design_metadata/README.txt", "All identifiers in this package are pseudonymized. Identity linkage is managed separately.\n")
        archive.writestr("02_core/sessions.csv", _csv_bytes(["session_id", "course_subject_id", "experiment_id", "version", "status", "started_at", "completed_at"], [[s.pk, _subject_uuid(s.student_id, job.course_id), s.version.experiment_id, s.version.version_number, s.status, s.started_at.isoformat(), s.completed_at.isoformat() if s.completed_at else ""] for s in sessions]))
        archive.writestr("02_core/attempts.csv", _csv_bytes(["attempt_id", "session_id", "course_subject_id", "question_code", "status", "is_correct", "score", "submitted_at"], [[a.pk, a.session_id, _subject_uuid(a.session.student_id, job.course_id), a.assignment.snapshot.code, a.status, a.is_correct, a.score, a.submitted_at.isoformat() if a.submitted_at else ""] for a in attempts]))
        archive.writestr("03_raw_events/assessment_events.csv", _csv_bytes(["event_id", "event_type", "event_version", "session_id", "attempt_id", "client_instance_id", "client_sequence_no", "server_ingest_sequence_no", "correlation_id", "server_received_at", "capture_mode", "quality_flags", "payload"], [[e.event_id, e.event_type, e.event_version, e.session_id, e.attempt_id, e.client_instance_id, e.client_sequence_no, e.server_ingest_sequence_no, e.correlation_id, e.server_received_at.isoformat(), e.capture_mode, json.dumps(e.quality_flags, ensure_ascii=False), json.dumps(e.payload, ensure_ascii=False)] for e in events]))
    digest = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
    job.status = ExportJob.Status.SUCCEEDED
    job.finished_at = timezone.now()
    job.progress_stage = "complete"
    job.artifact_path = zip_path
    job.artifact_sha256 = digest
    job.save(update_fields=["status", "finished_at", "progress_stage", "artifact_path", "artifact_sha256"])
    return job


def _subject_uuid(student_id, course_id):
    from apps.accounts.models import CourseSubject
    subject = CourseSubject.objects.filter(student_id=student_id, course_id=course_id).first()
    return str(subject.public_uuid) if subject else ""
