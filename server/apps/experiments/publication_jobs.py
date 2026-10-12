from datetime import timedelta
from threading import Event, Lock, Thread

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import PublishOperationJob, PublishPlan, PublishPlanExclusion
from .publication_services import (
    PublicationConflict,
    commit_publish_plan,
    generate_preview,
)


LEASE_SECONDS = 60
MAX_ATTEMPTS = 3
STALE_ERROR_CODES = {
    "PREVIEW_INVALIDATED",
    "PUBLISH_PLAN_EXPIRED",
    "PUBLISH_PLAN_STALE",
}


def enqueue_publish_operation(
    *,
    plan,
    actor,
    operation,
    expected_revision,
    idempotency_key="",
):
    if plan.plan_revision != expected_revision:
        raise PublicationConflict(
            "发布计划已被更新，请刷新后重试。",
            code="PUBLISH_PLAN_STALE",
        )
    if operation not in PublishOperationJob.Operation.values:
        raise ValueError("unsupported publish operation")
    idempotency_key = (idempotency_key or "").strip()[:64]
    if idempotency_key:
        existing = PublishOperationJob.objects.filter(
            requested_by=actor,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            return existing
    active = PublishOperationJob.objects.filter(
        publish_plan=plan,
        operation=operation,
        status__in=(
            PublishOperationJob.Status.QUEUED,
            PublishOperationJob.Status.RUNNING,
        ),
    ).first()
    if active is not None:
        return active
    try:
        return PublishOperationJob.objects.create(
            publish_plan=plan,
            operation=operation,
            expected_plan_revision=expected_revision,
            requested_by=actor,
            idempotency_key=idempotency_key,
            total=_participant_total(plan),
        )
    except IntegrityError:
        return PublishOperationJob.objects.get(
            publish_plan=plan,
            operation=operation,
            status__in=(
                PublishOperationJob.Status.QUEUED,
                PublishOperationJob.Status.RUNNING,
            ),
        )


def _participant_total(plan):
    selected = plan.class_plans.values_list("class_group_id", flat=True)
    excluded = PublishPlanExclusion.objects.filter(
        publish_plan_class__publish_plan=plan
    ).values_list("student_id", flat=True)
    return plan.experiment.course.enrollments.filter(
        class_group_id__in=selected,
        is_active=True,
        student__is_active=True,
    ).exclude(student_id__in=excluded).count()


def publish_operation_projection(job):
    return {
        "job_id": str(job.job_id),
        "operation": job.operation,
        "status": job.status,
        "status_url": f"/api/v1/teacher/publish-operation-jobs/{job.job_id}/",
        "plan_revision": job.expected_plan_revision,
        "stage": job.stage,
        "processed": job.processed,
        "total": job.total,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "preview_run_id": job.result_preview_run_id,
        "release_snapshot_id": job.result_release_snapshot_id,
    }


def claim_publish_operation(*, worker_id):
    now = timezone.now()
    with transaction.atomic():
        job = (
            PublishOperationJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=PublishOperationJob.Status.QUEUED)
                | Q(
                    status=PublishOperationJob.Status.RUNNING,
                    lease_expires_at__lt=now,
                    retry_count__lt=MAX_ATTEMPTS,
                )
            )
            .order_by("created_at")
            .first()
        )
        if job is None:
            return None
        if job.status == PublishOperationJob.Status.RUNNING:
            job.retry_count += 1
        job.status = PublishOperationJob.Status.RUNNING
        job.stage = (
            "selecting_questions"
            if job.operation == PublishOperationJob.Operation.PREVIEW
            else "creating_release"
        )
        job.locked_by = worker_id
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        job.save(
            update_fields=[
                "status",
                "stage",
                "locked_by",
                "retry_count",
                "started_at",
                "heartbeat_at",
                "lease_expires_at",
            ]
        )
        return job


def run_publish_operation(job, *, progress=None):
    try:
        plan = PublishPlan.objects.get(pk=job.publish_plan_id)
        if job.operation == PublishOperationJob.Operation.PREVIEW:
            preview = generate_preview(
                plan,
                job.requested_by,
                expected_revision=job.expected_plan_revision,
                progress=progress,
            )
            result_preview_run_id = preview.pk
            result_release_snapshot_id = None
        else:
            tasks = commit_publish_plan(
                plan,
                job.requested_by,
                expected_revision=job.expected_plan_revision,
                progress=progress,
            )
            result_preview_run_id = plan.preview_run_id
            result_release_snapshot_id = (
                tasks[0].release_snapshot_id if tasks else None
            )
    except (PublicationConflict, DjangoValidationError) as exc:
        code = getattr(exc, "code", None) or "PUBLISH_VALIDATION_FAILED"
        messages = getattr(exc, "messages", None) or [str(exc)]
        PublishOperationJob.objects.filter(pk=job.pk).update(
            status=(
                PublishOperationJob.Status.STALE
                if code in STALE_ERROR_CODES
                else PublishOperationJob.Status.FAILED
            ),
            stage="failed",
            error_code=code,
            error_message="；".join(messages)[:255],
            finished_at=timezone.now(),
            lease_expires_at=None,
        )
    except Exception as exc:
        current = PublishOperationJob.objects.get(pk=job.pk)
        if current.retry_count + 1 < MAX_ATTEMPTS:
            PublishOperationJob.objects.filter(pk=job.pk).update(
                status=PublishOperationJob.Status.QUEUED,
                stage="queued",
                retry_count=current.retry_count + 1,
                error_code="WORKER_INTERRUPTED",
                error_message=str(exc)[:255],
                locked_by="",
                lease_expires_at=None,
            )
        else:
            PublishOperationJob.objects.filter(pk=job.pk).update(
                status=PublishOperationJob.Status.FAILED,
                stage="failed",
                retry_count=current.retry_count + 1,
                error_code="WORKER_FAILED",
                error_message=str(exc)[:255],
                finished_at=timezone.now(),
                lease_expires_at=None,
            )
    else:
        PublishOperationJob.objects.filter(pk=job.pk).update(
            status=PublishOperationJob.Status.SUCCEEDED,
            stage="completed",
            processed=job.total,
            result_preview_run_id=result_preview_run_id,
            result_release_snapshot_id=result_release_snapshot_id,
            error_code="",
            error_message="",
            finished_at=timezone.now(),
            lease_expires_at=None,
        )
    return PublishOperationJob.objects.get(pk=job.pk)


def run_next_publish_operation(*, worker_id):
    job = claim_publish_operation(worker_id=worker_id)
    if job is None:
        return None
    stop = Event()
    progress_lock = Lock()
    progress_state = {"stage": job.stage, "processed": job.processed, "total": job.total}

    def record_progress(stage, processed, total):
        with progress_lock:
            progress_state.update(stage=stage, processed=processed, total=total)

    def heartbeat():
        from django.db import close_old_connections, connections

        close_old_connections()
        try:
            while not stop.wait(10):
                with progress_lock:
                    values = dict(progress_state)
                now = timezone.now()
                PublishOperationJob.objects.filter(
                    pk=job.pk,
                    status=PublishOperationJob.Status.RUNNING,
                    locked_by=worker_id,
                ).update(
                    **values,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                )
        finally:
            connections.close_all()

    heartbeat_thread = Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        return run_publish_operation(job, progress=record_progress)
    finally:
        stop.set()
        heartbeat_thread.join(timeout=2)
