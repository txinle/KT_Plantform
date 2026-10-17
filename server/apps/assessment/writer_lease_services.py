from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.db import transaction
from django.utils import timezone

from .models import Attempt

LEASE_DURATION = timedelta(seconds=30)


class WriterLeaseConflict(RuntimeError):
    """Another client currently owns the attempt writer lease."""


def _bind_locked_attempt(attempt):
    """Bind an internally locked row to the block that acquired its locks."""
    connection = transaction.get_connection(attempt._state.db)
    if not connection.in_atomic_block:
        raise WriterLeaseConflict('writer lease context requires an active transaction')
    attempt._writer_lock_context = (connection, connection.atomic_blocks[-1], attempt.pk,
                                    attempt.session_id, attempt.session.student_id)
    return attempt


def _validated_locked_attempt(attempt, current):
    context = getattr(current, '_writer_lock_context', None)
    connection = transaction.get_connection(current._state.db)
    session = current._state.fields_cache.get('session')
    if (context is None or context[0] is not connection or context[1] not in connection.atomic_blocks
            or current is not attempt or session is None
            or (current.pk, current.session_id, session.student_id) != context[2:]
            or session.pk != current.session_id):
        raise WriterLeaseConflict('writer lease locked context does not match')
    return current


def assert_attempt_writer_lease(
    attempt,
    *,
    lease_token=None,
    client_instance_id=None,
    now=None,
):
    """Require the active writer lease when one has been acquired.

    Existing pilots with no lease continue to work. Once a lease is present,
    every mutating request must present the matching token and client instance.
    """
    now = now or timezone.now()
    if not attempt.writer_lease_expires_at or attempt.writer_lease_expires_at <= now:
        return True
    try:
        token = uuid.UUID(str(lease_token)) if lease_token is not None else None
        client = uuid.UUID(str(client_instance_id)) if client_instance_id is not None else None
    except (TypeError, ValueError):
        raise WriterLeaseConflict("writer lease token is invalid") from None
    if token != attempt.writer_lease_token or client != attempt.writer_client_instance_id:
        raise WriterLeaseConflict("attempt is read-only for this client")
    return True


@dataclass(frozen=True)
class WriterLease:
    token: uuid.UUID
    client_instance_id: uuid.UUID
    expires_at: object


def acquire_attempt_writer_lease(attempt, *, client_instance_id, now=None, duration=LEASE_DURATION,
                                 _locked_attempt=None):
    now = now or timezone.now()
    client_instance_id = uuid.UUID(str(client_instance_id))
    with transaction.atomic():
        current = (_validated_locked_attempt(attempt, _locked_attempt) if _locked_attempt is not None
                   else Attempt.objects.select_for_update().get(pk=attempt.pk))
        active = current.writer_lease_expires_at and current.writer_lease_expires_at > now
        if active:
            if current.writer_client_instance_id != client_instance_id:
                raise WriterLeaseConflict("attempt writer lease is held by another client")
            if current.writer_lease_token is not None:
                return WriterLease(
                    current.writer_lease_token,
                    client_instance_id,
                    current.writer_lease_expires_at,
                )
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


def release_attempt_writer_lease(
    attempt,
    *,
    lease_token,
    client_instance_id,
    now=None,
    _locked_attempt=None,
):
    now = now or timezone.now()
    token = uuid.UUID(str(lease_token)); client = uuid.UUID(str(client_instance_id))
    with transaction.atomic():
        current = (_validated_locked_attempt(attempt, _locked_attempt) if _locked_attempt is not None
                   else Attempt.objects.select_for_update().get(pk=attempt.pk))
        active = (
            current.writer_lease_expires_at
            and current.writer_lease_expires_at > now
        )
        if active and (
            current.writer_lease_token != token
            or current.writer_client_instance_id != client
        ):
            raise WriterLeaseConflict("writer lease is not owned by client")
        if not any(
            (
                current.writer_lease_token,
                current.writer_client_instance_id,
                current.writer_lease_expires_at,
            )
        ):
            return
        current.writer_lease_token = None; current.writer_client_instance_id = None; current.writer_lease_expires_at = None
        current.save(update_fields=["writer_lease_token", "writer_client_instance_id", "writer_lease_expires_at"])
