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
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_export_snapshots")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    _FROZEN_FIELDS = frozenset({
        "course_id", "experiment_id", "research_study_id", "cutoff_at",
        "source_cutoff_at", "event_high_watermark", "schema_manifest_hash",
        "database_snapshot_strategy", "selected_versions", "filter_definition",
        "created_by_id",
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
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_export_jobs")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        return super().save(*args, **kwargs)
