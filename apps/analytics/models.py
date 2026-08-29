import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ExportSnapshot(models.Model):
    snapshot_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey("accounts.Course", on_delete=models.PROTECT, related_name="export_snapshots")
    experiment = models.ForeignKey("experiments.Experiment", null=True, blank=True, on_delete=models.PROTECT, related_name="export_snapshots")
    research_study = models.ForeignKey("accounts.ResearchStudy", null=True, blank=True, on_delete=models.PROTECT, related_name="export_snapshots")
    cutoff_at = models.DateTimeField(default=timezone.now)
    # Frozen source boundary. ``cutoff_at`` remains as a compatibility alias;
    # all new exports use the explicit source boundary fields below.
    source_cutoff_at = models.DateTimeField(default=timezone.now)
    event_high_watermark = models.PositiveBigIntegerField(default=0)
    schema_manifest_hash = models.CharField(max_length=64, blank=True, default="")
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidated_reason = models.CharField(max_length=255, blank=True, default="")
    database_snapshot_strategy = models.CharField(max_length=32, default="single_read_transaction")
    selected_versions = models.JSONField(default=list, blank=True)
    filter_definition = models.JSONField(default=dict, blank=True)
    scope_hash = models.CharField(max_length=64, blank=True, default="")
    coverage_run = models.ForeignKey(
        "analytics.RecordingCoverageRun",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="export_snapshots",
    )
    redacted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_export_snapshots")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    _FROZEN_FIELDS = frozenset({
        "course_id", "experiment_id", "research_study_id", "cutoff_at",
        "source_cutoff_at", "event_high_watermark", "schema_manifest_hash",
        "database_snapshot_strategy", "selected_versions", "filter_definition",
        "scope_hash", "coverage_run_id", "created_by_id",
    })

    def save(self, *args, **kwargs):
        """Keep the source boundary immutable after creation.

        Invalidation is the sole normal mutation and is performed by the
        governance service, so a rerun can never silently change provenance.
        """
        if not self._state.adding:
            update_fields = kwargs.get("update_fields")
            fields = set(update_fields) if update_fields is not None else set()
            if not fields or fields & self._FROZEN_FIELDS:
                previous = type(self).objects.only(*self._FROZEN_FIELDS).get(pk=self.pk)
                for field in self._FROZEN_FIELDS:
                    if getattr(previous, field) != getattr(self, field):
                        raise ValueError(f"ExportSnapshot.{field} is immutable")
        return super().save(*args, **kwargs)


class ExportJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "生成中"
        SUCCEEDED = "succeeded", "已完成"
        FAILED = "failed", "失败"
        EXPIRED = "expired", "已过期"
        INVALIDATED = "invalidated", "已失效"

    class Kind(models.TextChoices):
        RESEARCH = "research", "实名研究数据包"
        IDENTITY = "identity", "学生名册"

    job_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey("accounts.Course", on_delete=models.PROTECT, related_name="export_jobs")
    experiment = models.ForeignKey("experiments.Experiment", null=True, blank=True, on_delete=models.PROTECT, related_name="export_jobs")
    research_study = models.ForeignKey("accounts.ResearchStudy", null=True, blank=True, on_delete=models.PROTECT, related_name="export_jobs")
    snapshot = models.ForeignKey(ExportSnapshot, on_delete=models.PROTECT, related_name="jobs")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.RESEARCH)
    package = models.CharField(max_length=32, default="complete")
    selected_tables = models.JSONField(default=list, blank=True)
    selected_fields = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    locked_by = models.CharField(max_length=128, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    progress_stage = models.CharField(max_length=64, blank=True)
    artifact_path = models.CharField(max_length=512, blank=True)
    artifact_sha256 = models.CharField(max_length=64, blank=True)
    artifact_removed_at = models.DateTimeField(null=True, blank=True)
    artifact_removal_reason = models.CharField(max_length=64, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_export_jobs")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        return super().save(*args, **kwargs)

    def expire_artifact(self, *, reason="user_requested_removal"):
        from .services import remove_export_artifact

        return remove_export_artifact(self, reason=reason)


class RecordingCoverageImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("RecordingCoverage rows are immutable")

    def delete(self):
        raise ValueError("RecordingCoverage rows are immutable")

    def bulk_create(self, objs, **kwargs):
        raise ValueError("RecordingCoverage rows must be created through the coverage service")


class RecordingCoverageImmutableManager(
    models.Manager.from_queryset(RecordingCoverageImmutableQuerySet)
):
    pass


class RecordingCoverageRun(models.Model):
    class Status(models.TextChoices):
        BUILDING = "building", "生成中"
        COMPLETED = "completed", "已完成"
        FAILED = "failed", "失败"

    run_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope_type = models.CharField(max_length=32, default="session")
    scope_definition = models.JSONField(default=dict)
    scope_hash = models.CharField(max_length=64)
    coverage_version = models.CharField(max_length=32, default="coverage-v1")
    algorithm_version = models.CharField(max_length=64, default="recording-coverage-v1")
    code_version = models.CharField(max_length=64, blank=True)
    parameter_document = models.JSONField(default=dict)
    parameter_hash = models.CharField(max_length=64)
    source_cutoff_at = models.DateTimeField(default=timezone.now)
    source_high_water_marks = models.JSONField(default=dict)
    capture_contract_hashes = models.JSONField(default=list)
    source_hash = models.CharField(max_length=64)
    run_status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.COMPLETED
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    objects = RecordingCoverageImmutableManager()

    _FROZEN_FIELDS = frozenset(
        {
            "scope_type",
            "scope_definition",
            "scope_hash",
            "coverage_version",
            "algorithm_version",
            "code_version",
            "parameter_document",
            "parameter_hash",
            "source_cutoff_at",
            "source_high_water_marks",
            "capture_contract_hashes",
            "source_hash",
            "run_status",
        }
    )

    class Meta:
        ordering = ["-generated_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            for field in self._FROZEN_FIELDS:
                if getattr(previous, field) != getattr(self, field):
                    raise ValueError(f"RecordingCoverageRun.{field} is immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("RecordingCoverageRun rows are immutable")


class RecordingCoverageSession(models.Model):
    class OverallStatus(models.TextChoices):
        COMPLETE = "complete", "完整"
        PARTIAL = "partial", "部分"
        INSUFFICIENT = "insufficient", "证据不足"
        INVALID = "invalid", "无效"
        NOT_APPLICABLE = "not_applicable", "不适用"
        LEGACY_UNKNOWN = "legacy_unknown", "历史未知"

    run = models.ForeignKey(
        RecordingCoverageRun,
        on_delete=models.PROTECT,
        related_name="session_results",
    )
    session = models.ForeignKey(
        "assessment.ExperimentSession",
        on_delete=models.PROTECT,
        related_name="recording_coverage_results",
    )
    overall_status = models.CharField(max_length=24, choices=OverallStatus.choices)
    lifecycle_status = models.CharField(max_length=24, default="not_applicable")
    navigation_status = models.CharField(max_length=24, default="not_applicable")
    client_sequence_status = models.CharField(max_length=24, default="not_applicable")
    time_sync_status = models.CharField(max_length=24, default="not_applicable")
    telemetry_status = models.CharField(max_length=24, default="not_applicable")
    client_instance_count = models.PositiveIntegerField(default=0)
    sequence_gap_count = models.PositiveIntegerField(default=0)
    missing_sequence_ranges = models.JSONField(default=list)
    tail_gap_detected = models.BooleanField(default=False)
    missing_reasons = models.JSONField(default=list)
    analysis_eligibility = models.JSONField(default=dict)
    generated_at = models.DateTimeField(auto_now_add=True)

    objects = RecordingCoverageImmutableManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "session"], name="unique_coverage_run_session"
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("RecordingCoverageSession rows are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("RecordingCoverageSession rows are immutable")


class RecordingCoverageAttempt(models.Model):
    run = models.ForeignKey(
        RecordingCoverageRun,
        on_delete=models.PROTECT,
        related_name="attempt_results",
    )
    attempt = models.ForeignKey(
        "assessment.Attempt",
        on_delete=models.PROTECT,
        related_name="recording_coverage_results",
    )
    overall_status = models.CharField(max_length=24)
    enter_status = models.CharField(max_length=24, default="not_applicable")
    render_status = models.CharField(max_length=24, default="not_applicable")
    leave_status = models.CharField(max_length=24, default="not_applicable")
    response_chain_status = models.CharField(max_length=24, default="not_applicable")
    final_state_status = models.CharField(max_length=24, default="not_applicable")
    time_quality = models.CharField(max_length=32, default="unknown")
    response_state_count = models.PositiveIntegerField(default=0)
    sequence_gap_count = models.PositiveIntegerField(default=0)
    missing_reasons = models.JSONField(default=list)
    analysis_eligibility = models.JSONField(default=dict)
    derived_features = models.JSONField(default=dict)
    generated_at = models.DateTimeField(auto_now_add=True)

    objects = RecordingCoverageImmutableManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "attempt"], name="unique_coverage_run_attempt"
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("RecordingCoverageAttempt rows are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("RecordingCoverageAttempt rows are immutable")
