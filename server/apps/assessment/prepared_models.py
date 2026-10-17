"""Immutable publication products and a bounded repair queue."""
from django.db import models
from django.utils import timezone


class PreparedContentObject(models.Model):
    projection_hash = models.CharField(max_length=64, primary_key=True)
    descriptor = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)


class PreparedSessionArtifact(models.Model):
    session = models.OneToOneField('assessment.ExperimentSession', on_delete=models.CASCADE,
                                  related_name='prepared_artifact')
    preparation_version = models.CharField(max_length=64)
    ready = models.BooleanField(default=False)
    item_count = models.PositiveIntegerField(default=0)
    first_attempt = models.ForeignKey('assessment.Attempt', null=True, on_delete=models.PROTECT, related_name='+')
    manifest_descriptor = models.JSONField(default=dict)
    first_runtime = models.JSONField(default=dict)
    activation_receipt = models.JSONField(default=dict)
    activation_client_id = models.UUIDField(null=True)
    activation_execution_id = models.UUIDField(null=True)
    admission_token = models.CharField(max_length=128, blank=True)
    prepared_at = models.DateTimeField(default=timezone.now)


class PreparedAttemptProjection(models.Model):
    attempt = models.OneToOneField('assessment.Attempt', on_delete=models.CASCADE,
                                  related_name='prepared_projection')
    runtime = models.JSONField()
    content_descriptor = models.JSONField()


class PreparedSessionRepair(models.Model):
    participant = models.ForeignKey('experiments.ExperimentVersionParticipantSnapshot', on_delete=models.PROTECT)
    preparation_version = models.CharField(max_length=64)
    status = models.CharField(max_length=12, default='queued', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    requested_at = models.DateTimeField(default=timezone.now)
    error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['participant', 'preparation_version'],
                                               name='unique_prepared_participant_repair')]


class PreparedActivationCancellation(models.Model):
    execution_id = models.UUIDField(primary_key=True)
    session = models.ForeignKey('assessment.ExperimentSession', on_delete=models.PROTECT)
    admission_token = models.CharField(max_length=128)
    cancelled_at = models.DateTimeField(default=timezone.now)
