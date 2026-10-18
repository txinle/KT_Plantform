"""Durable confirmation-bound receipts independent of the deleted object tree."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class ResearchDeletionOperation(models.Model):
    operation_id = models.UUIDField(primary_key=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    course = models.ForeignKey('accounts.Course', on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    object_id = models.PositiveBigIntegerField()
    class_id = models.PositiveBigIntegerField(null=True)
    scope_fingerprint = models.CharField(max_length=64)
    frozen_scope = models.JSONField()
    confirmation_token_hash = models.CharField(max_length=64)
    state = models.CharField(max_length=12, default='pending', choices=[
        ('pending', '结果待确认'), ('succeeded', '已删除'), ('rejected', '未执行')])
    accepted_at = models.DateTimeField(default=timezone.now)
    execute_before = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, blank=True)
    public_message = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=['state', 'execute_before'], name='deletion_pending_expiry_idx')]
