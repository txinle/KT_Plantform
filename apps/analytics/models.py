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
    database_snapshot_strategy = models.CharField(max_length=32, default="single_read_transaction")
    selected_versions = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_export_snapshots")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ExportJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "生成中"
        SUCCEEDED = "succeeded", "已完成"
        FAILED = "failed", "失败"
        EXPIRED = "expired", "已过期"
        INVALIDATED = "invalidated", "已失效"

    class Kind(models.TextChoices):
        RESEARCH = "research", "去标识化研究包"
        IDENTITY = "identity", "身份映射"

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
