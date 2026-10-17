"""Durable whole-paper receipt and its leased processing queue."""
import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class SessionSubmission(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'queued', '等待处理'
        RUNNING = 'running', '处理中'
        COMPLETED = 'completed', '处理完成'
        FAILED = 'failed', '处理失败'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.OneToOneField('assessment.ExperimentSession', on_delete=models.PROTECT,
                                   related_name='submission_job')
    submission_token = models.UUIDField(unique=True)
    material_fingerprint = models.CharField(max_length=64)
    manifest = models.JSONField()
    accepted_at = models.DateTimeField(default=timezone.now)
    client_clicked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    locked_by = models.CharField(max_length=128, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'next_attempt_at'], name='session_submission_queue_idx')]
        constraints = [models.CheckConstraint(
            condition=(Q(status='running', lease_token__isnull=False, lease_expires_at__isnull=False)
                       | (~Q(status='running') & Q(lease_token__isnull=True, lease_expires_at__isnull=True))),
            name='session_submission_lease_state',
        )]
