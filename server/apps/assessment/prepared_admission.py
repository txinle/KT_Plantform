"""Prepared activation capacity owned by the PostgreSQL activation transaction."""
from django.conf import settings
from django.db import connection

from .admission import AdmissionUnavailable


# Fixed two-int advisory namespace; never share keys with legacy admission fences.
ACTIVATION_LOCK_NAMESPACE = 0x4B545041


class ActivationBusy(Exception):
    pass


class ActivationExecuting(Exception):
    pass


def acquire_activation_slot(*, student_id):
    if connection.vendor != 'postgresql' or not connection.in_atomic_block:
        raise AdmissionUnavailable('Prepared transaction admission requires PostgreSQL and an active transaction')
    capacity = settings.ASSESSMENT_ADMISSION_CAPACITY
    with connection.cursor() as cursor:
        for offset in range(capacity):
            slot = (student_id + offset) % capacity
            cursor.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', [ACTIVATION_LOCK_NAMESPACE, slot])
            if cursor.fetchone()[0]:
                return slot
    raise ActivationBusy()
