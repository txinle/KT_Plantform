from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditLog

from .class_lifecycle import lock_active_class_groups
from .enrollment_services import transfer_course_enrollment
from .forms import class_group_state_token_matches
from .models import ClassGroup, CourseEnrollment, StudentImportJob, User
from .student_import import ParsedStudent


IMPORT_PREVIEW_STALE_ERROR = "班级信息已更新，请重新上传名单并生成预览。"


class StudentImportJobError(Exception):
    def __init__(self, message, *, code="STUDENT_IMPORT_FAILED"):
        self.code = code
        super().__init__(message)


def claim_student_import_job(*, worker_id, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        job = (
            StudentImportJob.objects.select_for_update()
            .filter(
                models.Q(status=StudentImportJob.Status.QUEUED)
                | models.Q(
                    status=StudentImportJob.Status.RUNNING,
                    lease_expires_at__lt=now,
                )
            )
            .order_by("created_at")
            .first()
        )
        if job is None:
            return None
        job.status = StudentImportJob.Status.RUNNING
        job.locked_by = worker_id
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(minutes=5)
        job.progress_stage = "validating"
        job.save(
            update_fields=[
                "status",
                "locked_by",
                "started_at",
                "heartbeat_at",
                "lease_expires_at",
                "progress_stage",
            ]
        )
        return job


def _parsed_rows(job):
    try:
        rows = [ParsedStudent(**row) for row in job.rows]
    except (TypeError, ValueError) as exc:
        raise StudentImportJobError("导入名单数据无效，请重新上传名单。") from exc
    if not rows:
        raise StudentImportJobError("导入名单为空，请重新上传名单。")
    numbers = [row.student_number for row in rows]
    if len(numbers) != len(set(numbers)):
        raise StudentImportJobError("导入文件中存在重复学号。")
    if any(row.class_id != job.class_group_id for row in rows):
        raise StudentImportJobError(IMPORT_PREVIEW_STALE_ERROR, code="IMPORT_PREVIEW_STALE")
    return rows


def _existing_students(numbers):
    return {
        value: user
        for user in User.objects.filter(
            Q(student_number__in=numbers) | Q(username__in=numbers)
        )
        for value in (user.student_number, user.username)
        if value in numbers
    }


def _validate_existing_students(course, rows, existing, *, lock_enrollments=False):
    enrollment_query = CourseEnrollment.objects.filter(
        course=course,
        student_id__in=[user.pk for user in existing.values()],
    ).order_by("pk")
    if lock_enrollments:
        enrollment_query = enrollment_query.select_for_update()
    enrollments = {
        enrollment.student_id: enrollment for enrollment in enrollment_query
    }
    for row in rows:
        user = existing.get(row.student_number)
        if user is None:
            if row.source_class_id is not None:
                raise StudentImportJobError(
                    IMPORT_PREVIEW_STALE_ERROR, code="IMPORT_PREVIEW_STALE"
                )
            continue
        if user.role != User.Role.STUDENT:
            raise StudentImportJobError(f"学号与非学生账号冲突：{row.student_number}")
        if user.shown_name != row.display_name:
            raise StudentImportJobError(f"学号对应姓名不一致：{row.student_number}")
        enrollment = enrollments.get(user.pk)
        if (
            (enrollment.class_group_id if enrollment else None) != row.source_class_id
            or (enrollment is not None and not enrollment.is_active)
        ):
            raise StudentImportJobError(
                IMPORT_PREVIEW_STALE_ERROR, code="IMPORT_PREVIEW_STALE"
            )
    return enrollments


def _renew_progress(job, *, worker_id, processed_rows):
    now = timezone.now()
    updated = StudentImportJob.objects.filter(
        pk=job.pk,
        status=StudentImportJob.Status.RUNNING,
        locked_by=worker_id,
    ).update(
        processed_rows=processed_rows,
        progress_stage="hashing_passwords",
        heartbeat_at=now,
        lease_expires_at=now + timedelta(minutes=5),
    )
    if not updated:
        raise StudentImportJobError("导入任务状态已变化，请重新上传名单。")


def run_student_import_job(job, *, worker_id):
    rows = _parsed_rows(job)
    if job.class_group_id is None:
        raise StudentImportJobError(IMPORT_PREVIEW_STALE_ERROR, code="IMPORT_PREVIEW_STALE")
    class_group = ClassGroup.objects.filter(pk=job.class_group_id).first()
    if class_group is None or not class_group_state_token_matches(
        job.class_state_token, class_group
    ):
        raise StudentImportJobError(IMPORT_PREVIEW_STALE_ERROR, code="IMPORT_PREVIEW_STALE")

    numbers = [row.student_number for row in rows]
    existing = _existing_students(numbers)
    _validate_existing_students(job.course, rows, existing)

    password_hashes = {}
    missing_rows = [row for row in rows if row.student_number not in existing]
    _renew_progress(job, worker_id=worker_id, processed_rows=0)
    for index, row in enumerate(missing_rows, start=1):
        password_hashes[row.student_number] = make_password(f"S{row.student_number}")
        if index % 10 == 0 or index == len(missing_rows):
            _renew_progress(job, worker_id=worker_id, processed_rows=index)

    with transaction.atomic():
        locked_job = StudentImportJob.objects.select_for_update().get(pk=job.pk)
        if (
            locked_job.status != StudentImportJob.Status.RUNNING
            or locked_job.locked_by != worker_id
        ):
            raise StudentImportJobError("导入任务状态已变化，请重新上传名单。")
        try:
            current_class = ClassGroup.objects.get(pk=locked_job.class_group_id)
        except ClassGroup.DoesNotExist as exc:
            raise StudentImportJobError(
                IMPORT_PREVIEW_STALE_ERROR,
                code="IMPORT_PREVIEW_STALE",
            ) from exc
        with lock_active_class_groups(
            [current_class],
            inactive_error="目标班级已停用，不能新增学生。",
            lock_courses=True,
        ) as locked_classes:
            current_class = locked_classes[current_class.pk]
            if not class_group_state_token_matches(
                locked_job.class_state_token, current_class
            ):
                raise StudentImportJobError(
                    IMPORT_PREVIEW_STALE_ERROR,
                    code="IMPORT_PREVIEW_STALE",
                )
            existing = _existing_students(numbers)
            existing_enrollments = _validate_existing_students(
                locked_job.course, rows, existing, lock_enrollments=True
            )
            created_accounts = 0
            enrollments = 0
            transferred = 0
            skipped = 0
            now = timezone.now()
            for row in rows:
                student = existing.get(row.student_number)
                current_enrollment = (
                    existing_enrollments.get(student.pk) if student else None
                )
                if current_enrollment is not None:
                    if current_enrollment.class_group_id == current_class.pk:
                        skipped += 1
                    else:
                        source_class_id = current_enrollment.class_group_id
                        transfer_course_enrollment(current_enrollment, current_class)
                        transferred += 1
                        AuditLog.objects.create(
                            actor=locked_job.created_by,
                            action="student.class_transfer",
                            object_type="CourseEnrollment",
                            object_id=str(current_enrollment.pk),
                            details={
                                "course_id": locked_job.course_id,
                                "from_class_id": source_class_id,
                                "to_class_id": current_class.pk,
                                "job_id": str(locked_job.job_id),
                            },
                            ip_address=locked_job.request_ip,
                        )
                    continue
                if student is None:
                    encoded_password = password_hashes.get(row.student_number)
                    if encoded_password is None:
                        raise StudentImportJobError(
                            f"学生账号状态发生变化：{row.student_number}"
                        )
                    student = User.objects.create(
                        username=row.student_number,
                        student_number=row.student_number,
                        display_name=row.display_name,
                        role=User.Role.STUDENT,
                        password=encoded_password,
                        uses_initial_password=True,
                        initial_password_state_recorded_at=now,
                    )
                    created_accounts += 1
                CourseEnrollment.objects.create(
                    course=locked_job.course,
                    class_group=current_class,
                    student=student,
                    is_active=True,
                )
                enrollments += 1

            locked_job.status = StudentImportJob.Status.SUCCEEDED
            locked_job.processed_rows = locked_job.total_rows
            locked_job.created_accounts = created_accounts
            locked_job.enrollments = enrollments
            locked_job.transferred = transferred
            locked_job.skipped = skipped
            locked_job.progress_stage = "completed"
            locked_job.error_code = ""
            locked_job.error_message = ""
            locked_job.rows = []
            locked_job.locked_by = ""
            locked_job.lease_expires_at = None
            locked_job.heartbeat_at = now
            locked_job.finished_at = now
            locked_job.save(
                update_fields=[
                    "status",
                    "processed_rows",
                    "created_accounts",
                    "enrollments",
                    "transferred",
                    "skipped",
                    "progress_stage",
                    "error_code",
                    "error_message",
                    "rows",
                    "locked_by",
                    "lease_expires_at",
                    "heartbeat_at",
                    "finished_at",
                ]
            )
            AuditLog.objects.create(
                actor=locked_job.created_by,
                action="student.batch_import",
                object_type="ClassGroup",
                object_id=str(current_class.pk),
                details={
                    "course_id": locked_job.course_id,
                    "class_id": current_class.pk,
                    "job_id": str(locked_job.job_id),
                    "enrollments": enrollments,
                    "transferred": transferred,
                    "skipped": skipped,
                    "created_accounts": created_accounts,
                },
                ip_address=locked_job.request_ip,
            )
    return job


def fail_student_import_job(job, error):
    now = timezone.now()
    StudentImportJob.objects.filter(pk=job.pk).update(
        status=StudentImportJob.Status.FAILED,
        progress_stage="failed",
        error_code=error.code,
        error_message=str(error),
        rows=[],
        locked_by="",
        lease_expires_at=None,
        heartbeat_at=now,
        finished_at=now,
    )


def retry_student_import_job(job):
    retry_count = job.retry_count + 1
    terminal = retry_count >= 3
    now = timezone.now()
    updates = {
        "status": (
            StudentImportJob.Status.FAILED
            if terminal
            else StudentImportJob.Status.QUEUED
        ),
        "progress_stage": "failed" if terminal else "queued",
        "error_code": "STUDENT_IMPORT_FAILED" if terminal else "",
        "error_message": (
            "服务器处理名单时出现异常，请重新上传名单。" if terminal else ""
        ),
        "retry_count": retry_count,
        "locked_by": "",
        "lease_expires_at": None,
        "heartbeat_at": now,
        "finished_at": now if terminal else None,
    }
    if terminal:
        updates["rows"] = []
    StudentImportJob.objects.filter(pk=job.pk).update(**updates)
