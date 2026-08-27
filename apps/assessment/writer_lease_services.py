from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.db import transaction
from django.utils import timezone

from .models import Attempt

LEASE_DURATION = timedelta(seconds=30)


class WriterLeaseConflict(RuntimeError):
    """Another client currently owns the attempt writer lease."""


@dataclass(frozen=True)
class WriterLease:
    token: uuid.UUID
    client_instance_id: uuid.UUID
    expires_at: object


def acquire_attempt_writer_lease(attempt, *, client_instance_id, now=None, duration=LEASE_DURATION):
    now = now or timezone.now()
    client_instance_id = uuid.UUID(str(client_instance_id))
    with transaction.atomic():
        current = Attempt.objects.select_for_update().get(pk=attempt.pk)
        active = current.writer_lease_expires_at and current.writer_lease_expires_at > now
        if active and current.writer_client_instance_id != client_instance_id:
            raise WriterLeaseConflict("attempt writer lease is held by another client")
        token = uuid.uuid4()
        expires_at = now + duration
        current.writer_lease_token = token
        current.writer_client_instance_id = client_instance_id
        current.writer_lease_expires_at = expires_at
        current.save(update_fields=["writer_lease_token", "writer_client_instance_id", "writer_lease_expires_at"])
    return WriterLease(token, client_instance_id, expires_at)


def renew_attempt_writer_lease(attempt, *, lease_token, client_instance_id, now=None, duration=LEASE_DURATION):
    now = now or timezone.now()
    token = uuid.UUID(str(lease_token)); client = uuid.UUID(str(client_instance_id))
    with transaction.atomic():
        current = Attempt.objects.select_for_update().get(pk=attempt.pk)
        if current.writer_lease_token != token or current.writer_client_instance_id != client or not current.writer_lease_expires_at or current.writer_lease_expires_at <= now:
            raise WriterLeaseConflict("writer lease is not valid")
        current.writer_lease_expires_at = now + duration
        current.save(update_fields=["writer_lease_expires_at"])
    return WriterLease(token, client, current.writer_lease_expires_at)


def release_attempt_writer_lease(attempt, *, lease_token, client_instance_id):
    token = uuid.UUID(str(lease_token)); client = uuid.UUID(str(client_instance_id))
    with transaction.atomic():
        current = Attempt.objects.select_for_update().get(pk=attempt.pk)
        if current.writer_lease_token != token or current.writer_client_instance_id != client:
            raise WriterLeaseConflict("writer lease is not owned by client")
        current.writer_lease_token = None; current.writer_client_instance_id = None; current.writer_lease_expires_at = None
        current.save(update_fields=["writer_lease_token", "writer_client_instance_id", "writer_lease_expires_at"])

