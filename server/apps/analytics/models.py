import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Now
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
    source_bundle = models.JSONField(null=True, blank=True)
    source_bundle_hash = models.CharField(max_length=64, blank=True, default="")
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
        "source_bundle", "source_bundle_hash",
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


class StudentExperimentResultSnapshot(models.Model):
    """Permission-trimmed student result projection cache."""

    class_task = models.ForeignKey(
        "experiments.ClassExperimentTask",
        on_delete=models.CASCADE,
        related_name="student_result_snapshots",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_experiment_result_snapshots",
    )
    source_signature = models.CharField(max_length=64)
    projection_version = models.CharField(max_length=64, default="student-result-v1")
    payload = models.JSONField()
    generated_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["class_task", "student"],
                name="unique_student_experiment_result_snapshot",
            ),
        ]
        indexes = [
            models.Index(
                fields=["class_task", "source_signature"],
                name="student_result_snap_sig_idx",
            ),
        ]


class StudentClassTaskComparison(models.Model):
    """Shared summary; raw cohort facts are read only by the background worker."""

    class_task = models.OneToOneField("experiments.ClassExperimentTask", on_delete=models.CASCADE, related_name="student_comparison")
    source_revision = models.PositiveBigIntegerField(default=0)
    source_signature = models.CharField(max_length=64, blank=True, default='', db_default='')
    payload = models.JSONField(default=dict)
    means = models.JSONField(default=dict, db_default={})
    full = models.JSONField(default=dict, db_default={})
    projection_version = models.PositiveSmallIntegerField(default=0, db_default=0)
    status = models.CharField(max_length=16, default="queued", choices=[(value, value) for value in ("queued", "running", "ready", "failed")])
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")


class StudentClassLearningComparison(models.Model):
    projection_version = models.PositiveSmallIntegerField(default=0, db_default=0)
    status = models.CharField(max_length=16, default="queued", choices=[(value, value) for value in ("queued", "running", "ready", "failed")])
    attempts = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    class_group = models.OneToOneField("accounts.ClassGroup", on_delete=models.CASCADE, related_name="student_learning_comparison")
    payload = models.JSONField(default=dict)
    generated_at = models.DateTimeField(default=timezone.now)
    next_refresh_at = models.DateTimeField(default=timezone.now, db_index=True)


class StudentTaskComparisonMember(models.Model):
    comparison = models.ForeignKey(StudentClassTaskComparison, on_delete=models.CASCADE, related_name="members")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="task_comparison_memberships")
    rank = models.JSONField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["comparison", "student"], name="unique_task_comparison_student")]


class StudentLearningComparisonMember(models.Model):
    comparison = models.ForeignKey(StudentClassLearningComparison, on_delete=models.CASCADE, related_name="members")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="learning_comparison_memberships")
    payload = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["comparison", "student"], name="unique_learning_comp_student")]


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
    request_fingerprint = models.CharField(max_length=64, blank=True, default="", db_index=True)
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


class ResearchExportAssetQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("ResearchExportAsset rows are immutable")

    def delete(self):
        raise ValueError("ResearchExportAsset rows are immutable")


class ResearchExportAsset(models.Model):
    class Kind(models.TextChoices):
        COURSE = "course", "课程研究资产"
        EXPERIMENT_SESSIONS = "experiment_sessions", "实验 Session 资产"

    asset_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    course = models.ForeignKey(
        "accounts.Course", on_delete=models.PROTECT, related_name="research_export_assets"
    )
    experiment = models.ForeignKey(
        "experiments.Experiment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="research_export_assets",
    )
    contract_version = models.CharField(max_length=32, default="research-export-v2")
    content_hash = models.CharField(max_length=64)
    artifact_path = models.CharField(max_length=512)
    artifact_sha256 = models.CharField(max_length=64)
    row_counts = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ResearchExportAssetQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "asset_id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "kind",
                    "course",
                    "experiment",
                    "contract_version",
                    "content_hash",
                ],
                name="analytics_unique_research_export_asset",
                nulls_distinct=False,
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("ResearchExportAsset rows are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("ResearchExportAsset rows are immutable")


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


class ResearchSessionMaterialization(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "生成中"
        SUCCEEDED = "succeeded", "已完成"
        FAILED = "failed", "失败"

    materialization_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        "assessment.ExperimentSession", on_delete=models.CASCADE,
        related_name="research_materializations",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    is_current = models.BooleanField(default=False)
    source_cutoff_at = models.DateTimeField(default=timezone.now)
    event_high_watermark = models.PositiveBigIntegerField(default=0)
    grade_high_watermark = models.JSONField(default=dict)
    source_high_watermarks = models.JSONField(default=dict)
    source_revision = models.PositiveBigIntegerField(default=0, db_index=True)
    projection_version = models.CharField(max_length=64, default="research-session-v1")
    locked_by = models.CharField(max_length=128, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_default=Now())
    priority = models.PositiveSmallIntegerField(default=0, db_default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    session_summary = models.JSONField(default=dict)
    session_row = models.JSONField(default=list)
    quality_rows = models.JSONField(default=list)
    row_counts = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "materialization_id"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at", "priority", "created_at"], name="analytics_retry_claim_idx", condition=models.Q(status__in=("queued", "running"))),
            models.Index(fields=["session", "projection_version", "source_revision", "status"], name="analytics_revision_job_idx"),
            models.Index(
                fields=["status", "lease_expires_at", "created_at"],
                name="analytics_research_claim_idx",
            ),
            models.Index(
                fields=["is_current", "last_reconciled_at"],
                name="analytics_reconcile_idx",
            ),
            models.Index(
                fields=["status", "activated_at", "superseded_at"],
                name="analytics_result_window_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session"], condition=models.Q(is_current=True),
                name="analytics_one_current_research_session",
            ),
            models.UniqueConstraint(
                fields=["session"], condition=models.Q(status__in=("queued", "running")),
                name="analytics_one_active_research_session_job",
            ),
        ]


class ResearchSessionQuality(models.Model):
    materialization = models.ForeignKey(
        ResearchSessionMaterialization, on_delete=models.CASCADE, related_name="quality_entries"
    )
    ordinal = models.PositiveIntegerField()
    attempt_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    question_ref = models.CharField(max_length=32, blank=True, default="")
    rule = models.CharField(max_length=128)
    state = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ["ordinal"]
        constraints = [models.UniqueConstraint(fields=["materialization", "ordinal"], name="unique_research_quality_ordinal")]
        indexes = [models.Index(fields=["materialization", "state", "ordinal"], name="analytics_quality_state_idx")]


class ResearchSessionResponse(models.Model):
    materialization = models.ForeignKey(
        ResearchSessionMaterialization, on_delete=models.CASCADE, related_name="response_rows"
    )
    ordinal = models.PositiveIntegerField()
    question_ref = models.CharField(max_length=32, blank=True, default="")
    assigned_order = models.PositiveIntegerField(null=True, blank=True)
    first_visit_order = models.PositiveIntegerField(null=True, blank=True)
    row = models.JSONField(default=list)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ["ordinal"]
        constraints = [models.UniqueConstraint(
            fields=["materialization", "ordinal"],
            name="unique_research_materialization_response_ordinal",
        )]
        indexes = [models.Index(
            fields=["materialization", "question_ref", "ordinal"],
            name="analytics_response_lookup_idx",
        )]


class ResearchSessionVisit(models.Model):
    materialization = models.ForeignKey(
        ResearchSessionMaterialization, on_delete=models.CASCADE, related_name="visit_rows"
    )
    ordinal = models.PositiveIntegerField()
    question_ref = models.CharField(max_length=32, blank=True, default="")
    visit_key = models.CharField(max_length=64, blank=True, default="")
    visit_index = models.PositiveIntegerField(null=True, blank=True)
    entered_at = models.CharField(max_length=40, blank=True, default="")
    row = models.JSONField(default=list)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ["ordinal"]
        constraints = [models.UniqueConstraint(
            fields=["materialization", "ordinal"],
            name="unique_research_materialization_visit_ordinal",
        )]
        indexes = [models.Index(
            fields=["materialization", "question_ref", "visit_key", "ordinal"],
            name="analytics_visit_lookup_idx",
        )]


class ResearchSessionBehavior(models.Model):
    materialization = models.ForeignKey(
        ResearchSessionMaterialization, on_delete=models.CASCADE, related_name="behavior_rows"
    )
    ordinal = models.PositiveIntegerField()
    question_ref = models.CharField(max_length=32, blank=True, default="")
    visit_key = models.CharField(max_length=64, blank=True, default="")
    sequence = models.PositiveIntegerField(null=True, blank=True)
    category = models.CharField(max_length=64, blank=True, default="")
    action = models.CharField(max_length=128, blank=True, default="")
    occurred_at = models.CharField(max_length=40, blank=True, default="")
    row = models.JSONField(default=list)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ["ordinal"]
        constraints = [models.UniqueConstraint(
            fields=["materialization", "ordinal"],
            name="unique_research_materialization_behavior_ordinal",
        )]
        indexes = [models.Index(
            fields=["materialization", "question_ref", "visit_key", "ordinal"],
            name="analytics_behavior_lookup_idx",
        )]
