"""Retire derived research files as part of an explicit domain deletion."""

import base64
import csv
import io
import threading
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import close_old_connections, connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.accounts.models import Course, DataPurgeRequest
from apps.assessment.models import ExperimentSession, SimulationSnapshot
from apps.experiments.models import ExperimentClassSnapshot, ExperimentVersion

from .models import ExportJob, ExportSnapshot, ResearchArtifactCleanupJob


ARTIFACT_CLEANUP_SCOPE = "research_artifacts"
CLEANUP_LEASE_SECONDS = 90
CLEANUP_HEARTBEAT_SECONDS = 20
CLEANUP_MAX_ATTEMPTS = 8
CLEANUP_RETRY_DELAYS = (5, 10, 20, 40, 80, 160, 300)


def affected_export_snapshots(*, course_id, experiment_ids=None, class_id=None,
                              question_ids=None, coverage_run_ids=()):
    """Use the frozen selection, including multi-experiment exports without an FK."""
    if question_ids is not None:
        # Whole-course question libraries depend only on the snapshot's job kinds.
        jobs = ExportJob.objects.filter(snapshot_id=OuterRef("pk"))
        return (
            ExportSnapshot.objects.filter(course_id=course_id)
            .alias(
                has_jobs=Exists(jobs),
                has_research_jobs=Exists(jobs.filter(kind=ExportJob.Kind.RESEARCH)),
            )
            .filter(Q(has_jobs=False) | Q(has_research_jobs=True))
            .defer("source_bundle")
        )
    experiments = {int(value) for value in experiment_ids or ()}
    version_ids = set(ExperimentVersion.objects.filter(experiment_id__in=experiments)
                      .values_list("pk", flat=True))
    class_snapshot_ids = set(str(value) for value in ExperimentClassSnapshot.objects.filter(
        source_class_group_id=class_id).values_list("pk", flat=True)) if class_id else set()
    session_ids = {str(value) for value in ExperimentSession.objects.filter(
        Q(class_snapshot__source_class_group_id=class_id) | Q(class_task__class_group_id=class_id)
    ).values_list("pk", flat=True)} if class_id else set()
    run_ids = {str(value) for value in coverage_run_ids}
    selected = []
    candidates = ExportSnapshot.objects.filter(
        Q(course_id=course_id) | Q(research_study__isnull=False)).prefetch_related("jobs")
    for snapshot in candidates:
        if str(snapshot.coverage_run_id) in run_ids:
            selected.append(snapshot.pk)
            continue
        scope = snapshot.filter_definition or {}
        frozen_experiments = {str(value) for value in scope.get("experiment_ids", [])}
        if scope.get("experiment_id"):
            frozen_experiments.add(str(scope["experiment_id"]))
        frozen_versions = {str(row.get("id")) for row in snapshot.selected_versions or []}
        encoded_sessions = (snapshot.source_bundle or {}).get("tables", {}).get("sessions")
        frozen_sessions = None
        if encoded_sessions is not None:
            body = base64.b64decode(encoded_sessions).decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(body)))
            frozen_experiments.update(row["experiment_id"] for row in rows if row.get("experiment_id"))
            frozen_sessions = {row["session_id"] for row in rows if row.get("session_id")}
        if experiments and not (
            snapshot.experiment_id in experiments
            or frozen_experiments.intersection(map(str, experiments))
            or frozen_versions.intersection(map(str, version_ids))
            or (snapshot.course_id == course_id and not frozen_experiments and not frozen_versions and snapshot.experiment_id is None)
        ):
            continue
        if class_id is not None:
            if frozen_sessions is not None and not frozen_sessions.intersection(session_ids):
                continue
            if scope.get("class_id") and str(scope["class_id"]) != str(class_id):
                continue
            if scope.get("class_snapshot_id") and str(scope["class_snapshot_id"]) not in class_snapshot_ids:
                continue
            if scope.get("session_id") and str(scope["session_id"]) not in session_ids:
                continue
        selected.append(snapshot.pk)
    return ExportSnapshot.objects.filter(pk__in=selected)


@transaction.atomic
def schedule_artifact_cleanup(*, course_id, files, reason, actor=None):
    files = list({(item["kind"], item["path"]): dict(item) for item in files}.values())
    if not files:
        return None
    cleanup = DataPurgeRequest.objects.create(
        scope_type=ARTIFACT_CLEANUP_SCOPE,
        scope_definition={"course_id": course_id, "files": files},
        requested_by=actor or Course.objects.get(pk=course_id).owner,
        reason_code=reason,
        status=DataPurgeRequest.Status.APPROVED,
    )
    ResearchArtifactCleanupJob.objects.create(cleanup=cleanup, remaining_files=files)
    return cleanup.purge_request_id


def _public_cleanup_error(job):
    if not job.last_error:
        return ""
    if job.state == job.State.FAILED:
        return "文件清理失败，已停止自动重试，请联系管理员。"
    return "部分文件暂未清理完成，后台将自动重试。"


def _save_cleanup_summary(job):
    status = (
        DataPurgeRequest.Status.EXECUTED if job.state == job.State.COMPLETED
        else DataPurgeRequest.Status.FAILED if job.state == job.State.FAILED
        else DataPurgeRequest.Status.APPROVED
    )
    DataPurgeRequest.objects.filter(pk=job.cleanup_id).update(
        status=status, executed_at=job.completed_at,
        result_counts={
            "remaining_files": len(job.remaining_files),
            "retained_files": len(job.retained_files),
            "attempts": job.attempts, "error": _public_cleanup_error(job),
        },
    )


@transaction.atomic(durable=True)
def claim_artifact_cleanup(*, cleanup_pk=None, course_id=None, now=None):
    now = now or timezone.now()
    while True:
        candidates = ResearchArtifactCleanupJob.objects.select_for_update(skip_locked=True).filter(
            Q(state="pending", next_attempt_at__lte=now)
            | Q(state="running", lease_expires_at__lte=now),
        )
        if cleanup_pk is not None:
            candidates = candidates.filter(cleanup_id=cleanup_pk)
        if course_id is not None:
            candidates = candidates.filter(cleanup__scope_definition__course_id=course_id)
        job = candidates.order_by("next_attempt_at", "pk").first()
        if job is None:
            return None
        if job.attempts >= CLEANUP_MAX_ATTEMPTS:
            job.state = job.State.FAILED
            job.lease_token = job.lease_expires_at = None
            job.last_error = job.last_error or "文件清理进程在最后一次执行期间失去租约。"
            job.save(update_fields=["state", "lease_token", "lease_expires_at", "last_error"])
            _save_cleanup_summary(job)
            continue
        job.state = job.State.RUNNING
        job.attempts += 1
        job.lease_token = uuid4()
        job.lease_expires_at = now + timedelta(seconds=CLEANUP_LEASE_SECONDS)
        job.save(update_fields=["state", "attempts", "lease_token", "lease_expires_at"])
        _save_cleanup_summary(job)
        return job


def renew_artifact_cleanup_lease(job_id, *, lease_token, now=None):
    now = now or timezone.now()
    return bool(ResearchArtifactCleanupJob.objects.filter(
        pk=job_id, state="running", lease_token=lease_token, lease_expires_at__gt=now,
    ).update(lease_expires_at=now + timedelta(seconds=CLEANUP_LEASE_SECONDS)))


def _owns_cleanup_lease(job):
    return ResearchArtifactCleanupJob.objects.filter(
        pk=job.pk, state="running", lease_token=job.lease_token,
        lease_expires_at__gt=timezone.now(),
    ).exists()


@contextmanager
def _cleanup_heartbeat(job):
    stopped, lost = threading.Event(), threading.Event()

    def renew():
        close_old_connections()
        try:
            while not stopped.wait(CLEANUP_HEARTBEAT_SECONDS):
                try:
                    renewed = renew_artifact_cleanup_lease(job.pk, lease_token=job.lease_token)
                except Exception:
                    renewed = False
                if not renewed:
                    lost.set()
                    return
        finally:
            connection.close()

    heartbeat = threading.Thread(target=renew, name="research-artifact-cleanup-lease", daemon=True)
    heartbeat.start()
    try:
        yield lost
    finally:
        stopped.set()
        heartbeat.join()


def _delete_artifact(item):
    kind, value = item["kind"], item["path"]
    if not isinstance(value, str) or not value:
        raise ValueError("文件路径为空或格式无效。")
    if kind == "export":
        root = (Path(settings.MEDIA_ROOT) / "exports").resolve()
        path = Path(value).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("导出文件路径不在允许的导出目录内。")
        path.unlink(missing_ok=True)
        return None
    if kind != "storage":
        raise ValueError("无法识别文件清理类型。")
    key = PurePosixPath(value)
    if key.is_absolute() or PureWindowsPath(value).drive or "\\" in value or ".." in key.parts or str(key) == ".":
        raise ValueError("存储文件路径无效。")
    if SimulationSnapshot.objects.filter(blob_storage_key=value).exists():
        return "still_referenced"
    try:
        default_storage.delete(value)
    except FileNotFoundError:
        pass
    return None


@transaction.atomic
def _finish_artifact_cleanup(claimed, *, remaining, retained, errors):
    now = timezone.now()
    job = ResearchArtifactCleanupJob.objects.select_for_update().filter(
        pk=claimed.pk, state="running", lease_token=claimed.lease_token,
        lease_expires_at__gt=now,
    ).first()
    if job is None:
        return False
    job.remaining_files, job.retained_files = remaining, retained
    job.last_error = "; ".join(errors)[:4000]
    job.lease_token = job.lease_expires_at = None
    if not remaining:
        job.state, job.completed_at = job.State.COMPLETED, now
    elif job.attempts >= CLEANUP_MAX_ATTEMPTS:
        job.state = job.State.FAILED
    else:
        job.state = job.State.PENDING
        job.next_attempt_at = now + timedelta(seconds=CLEANUP_RETRY_DELAYS[job.attempts - 1])
    job.save(update_fields=[
        "state", "remaining_files", "retained_files", "last_error", "lease_token",
        "lease_expires_at", "next_attempt_at", "completed_at",
    ])
    _save_cleanup_summary(job)
    return job.state == job.State.COMPLETED


def run_artifact_cleanup(job):
    # A real outer transaction must not survive into filesystem operations.
    with transaction.atomic(durable=True):
        owned = _owns_cleanup_lease(job)
    if not owned:
        return False
    remaining, retained, errors = [], list(job.retained_files), []
    with _cleanup_heartbeat(job) as lost:
        for item in job.remaining_files:
            if lost.is_set() or not _owns_cleanup_lease(job):
                return False
            try:
                reason = _delete_artifact(item)
            except Exception as error:
                remaining.append(item)
                errors.append(f"{type(error).__name__}: {error}")
            else:
                if reason:
                    retained.append({**item, "reason": reason})
        if lost.is_set():
            return False
        return _finish_artifact_cleanup(job, remaining=remaining, retained=retained, errors=errors)


def process_artifact_cleanup(cleanup_pk):
    """Explicit, due-only worker entry point; never called by commit or GET."""
    job = claim_artifact_cleanup(cleanup_pk=cleanup_pk)
    if job is not None:
        return run_artifact_cleanup(job)
    return ResearchArtifactCleanupJob.objects.filter(cleanup_id=cleanup_pk, state="completed").exists()


def retry_artifact_cleanups(*, course_id=None):
    """Explicitly drain eligible jobs; retry delays and leases remain authoritative."""
    completed = 0
    while (job := claim_artifact_cleanup(course_id=course_id)) is not None:
        completed += bool(run_artifact_cleanup(job))
    return completed


def cleanup_state(cleanup_id):
    if not cleanup_id:
        return {"cleanup_pending": False, "cleanup_id": None, "state": "completed",
                "attempts": 0, "next_retry_at": None, "error": "", "retained_count": 0}
    cleanup = DataPurgeRequest.objects.select_related("artifact_cleanup_job").get(
        purge_request_id=cleanup_id, scope_type=ARTIFACT_CLEANUP_SCOPE,
    )
    try:
        job = cleanup.artifact_cleanup_job
    except ResearchArtifactCleanupJob.DoesNotExist:
        # Completed historical purges intentionally have no new queue item.
        state = "completed" if cleanup.status == DataPurgeRequest.Status.EXECUTED else "failed" if cleanup.status == DataPurgeRequest.Status.FAILED else "pending"
        return {"cleanup_pending": state != "completed", "cleanup_id": str(cleanup_id), "state": state,
                "attempts": 0, "next_retry_at": None, "error": "", "retained_count": 0}
    return {
        "cleanup_pending": job.state != job.State.COMPLETED,
        "cleanup_id": str(cleanup_id), "state": job.state, "attempts": job.attempts,
        "next_retry_at": job.next_attempt_at if job.state == job.State.PENDING else None,
        "error": _public_cleanup_error(job), "retained_count": len(job.retained_files),
    }


def retire_research_exports(*, course_id, experiment_ids=None, class_id=None,
                            question_ids=None, coverage_run_ids=(), reason, actor=None,
                            extra_files=()):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("研究数据清理必须位于课程事务内。")
    # A repeatable-read export waiting for this course lock must serialize/retry,
    # rather than freeze a pre-deletion snapshot after the deletion commits.
    Course.objects.filter(pk=course_id).update(updated_at=timezone.now())
    snapshots = affected_export_snapshots(course_id=course_id, experiment_ids=experiment_ids,
        class_id=class_id, question_ids=question_ids, coverage_run_ids=coverage_run_ids)
    snapshot_ids = list(snapshots.values_list("pk", flat=True))
    jobs = list(ExportJob.objects.select_for_update().filter(snapshot_id__in=snapshot_ids).order_by("pk"))
    files = [{"kind": "export", "path": job.artifact_path} for job in jobs if job.artifact_path]
    files.extend(extra_files)
    cleanup_id = schedule_artifact_cleanup(course_id=course_id, files=files, reason=reason, actor=actor)
    ExportJob.objects.filter(pk__in=[job.pk for job in jobs]).delete()
    ExportSnapshot.objects.filter(pk__in=snapshot_ids).defer("source_bundle").delete()
    return {"export_count": len(jobs), **cleanup_state(cleanup_id)}


def retire_course_question_exports(*, course_id, question_ids, reason):
    return retire_research_exports(course_id=course_id, question_ids=question_ids, reason=reason)
