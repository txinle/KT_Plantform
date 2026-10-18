"""Durable work state separate from the immutable deletion file manifest."""

from django.db import models
from django.utils import timezone


class ResearchArtifactCleanupJob(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "等待清理"
        RUNNING = "running", "清理中"
        COMPLETED = "completed", "已完成"
        FAILED = "failed", "清理失败"

    cleanup = models.OneToOneField(
        "accounts.DataPurgeRequest", on_delete=models.PROTECT,
        related_name="artifact_cleanup_job",
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    remaining_files = models.JSONField(default=list)
    retained_files = models.JSONField(default=list)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "next_attempt_at"], name="artifact_cleanup_due_idx"),
            models.Index(fields=["state", "lease_expires_at"], name="artifact_cleanup_lease_idx"),
        ]
