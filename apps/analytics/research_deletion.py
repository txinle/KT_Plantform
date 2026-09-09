"""Retire derived research files as part of an explicit domain deletion."""

import base64
import csv
import io
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Course, DataPurgeRequest
from apps.assessment.models import ExperimentSession
from apps.experiments.models import ExperimentClassSnapshot, ExperimentVersion

from .models import ExportJob, ExportSnapshot


ARTIFACT_CLEANUP_SCOPE = "research_artifacts"


def affected_export_snapshots(*, course_id, experiment_ids=None, class_id=None,
                              question_ids=None, coverage_run_ids=()):
    """Use the frozen selection, including multi-experiment exports without an FK."""
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
        if question_ids is not None:
            # Course question libraries are frozen whole, even for a single student.
            if snapshot.course_id == course_id and (not snapshot.jobs.exists() or any(job.kind == ExportJob.Kind.RESEARCH for job in snapshot.jobs.all())):
                selected.append(snapshot.pk)
            continue
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


def schedule_artifact_cleanup(*, course_id, files, reason, actor=None):
    files = list({(item["kind"], item["path"]): item for item in files if item["path"]}.values())
    if not files:
        return None
    cleanup = DataPurgeRequest.objects.create(
        scope_type=ARTIFACT_CLEANUP_SCOPE,
        scope_definition={"course_id": course_id, "files": files},
        requested_by=actor or Course.objects.get(pk=course_id).owner,
        reason_code=reason,
        status=DataPurgeRequest.Status.APPROVED,
    )
    transaction.on_commit(lambda: process_artifact_cleanup(cleanup.pk))
    return cleanup.purge_request_id


def process_artifact_cleanup(cleanup_pk):
    """A durable retry keeps a filesystem failure from resurrecting deleted exports."""
    with transaction.atomic():
        cleanup = DataPurgeRequest.objects.select_for_update().get(
            pk=cleanup_pk, scope_type=ARTIFACT_CLEANUP_SCOPE)
        if cleanup.status == DataPurgeRequest.Status.EXECUTED:
            return True
        remaining = []
        error = ""
        for item in cleanup.scope_definition.get("files", []):
            try:
                if item["kind"] == "storage":
                    default_storage.delete(item["path"])
                else:
                    path = Path(item["path"]).resolve()
                    root = (Path(settings.MEDIA_ROOT) / "exports").resolve()
                    if not path.is_relative_to(root) or path == root:
                        raise ValueError("导出文件路径不在允许的导出目录内。")
                    path.unlink(missing_ok=True)
                    if path.parent != root and path.parent.is_dir() and not any(path.parent.iterdir()):
                        path.parent.rmdir()
            except (OSError, ValueError) as exc:
                remaining.append(item)
                error = str(exc)
        cleanup.scope_definition = {**cleanup.scope_definition, "files": remaining}
        cleanup.result_counts = {"remaining_files": len(remaining), "error": error}
        cleanup.status = DataPurgeRequest.Status.FAILED if remaining else DataPurgeRequest.Status.EXECUTED
        cleanup.executed_at = None if remaining else timezone.now()
        cleanup.save(update_fields=["scope_definition", "result_counts", "status", "executed_at"])
        return not remaining


def retry_artifact_cleanups(*, course_id=None):
    pending = DataPurgeRequest.objects.filter(scope_type=ARTIFACT_CLEANUP_SCOPE).exclude(
        status=DataPurgeRequest.Status.EXECUTED)
    if course_id is not None:
        pending = pending.filter(scope_definition__course_id=course_id)
    return sum(process_artifact_cleanup(pk) for pk in pending.values_list("pk", flat=True))


def cleanup_state(cleanup_id):
    if not cleanup_id:
        return {"cleanup_pending": False, "cleanup_id": None}
    cleanup = DataPurgeRequest.objects.get(purge_request_id=cleanup_id,
                                          scope_type=ARTIFACT_CLEANUP_SCOPE)
    return {"cleanup_pending": cleanup.status != DataPurgeRequest.Status.EXECUTED,
            "cleanup_id": str(cleanup_id)}


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
    ExportSnapshot.objects.filter(pk__in=snapshot_ids).delete()
    return {"export_count": len(jobs), **cleanup_state(cleanup_id)}


def retire_course_question_exports(*, course_id, question_ids, reason):
    return retire_research_exports(course_id=course_id, question_ids=question_ids, reason=reason)
