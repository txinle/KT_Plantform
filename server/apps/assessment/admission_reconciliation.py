"""Bounded reconciliation using durable receipts or a locked cancellation fence."""
from collections import Counter

from django.db import DatabaseError, connection, transaction

from .admission import AdmissionCoordinator, AdmissionExecution, AdmissionInvalid, AdmissionUnavailable
from .models import ExperimentSession, PreparedActivationCancellation, PreparedSessionArtifact


_ARTIFACT_FIELDS = ('session_id', 'session__status', 'activation_receipt',
                    'activation_execution_id', 'admission_token')


def _set_timeouts():
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('statement_timeout', '1000', true)")
            cursor.execute("SELECT set_config('lock_timeout', '100', true)")


def _committed_receipt(artifact):
    receipt = artifact['activation_receipt']
    return bool(
        artifact['session__status'] != ExperimentSession.Status.PREPARED
        and artifact['activation_execution_id'] and artifact['admission_token']
        and isinstance(receipt, dict)
        and str(receipt.get('session_id')) == str(artifact['session_id'])
        and receipt.get('admission_execution_id') == str(artifact['activation_execution_id'])
    )


def _matches(artifact, execution):
    return bool(_committed_receipt(artifact)
                and str(artifact['activation_execution_id']) == execution.execution_id
                and artifact['admission_token'] == execution.token)


def _cancel_under_lock(execution, session_id):
    if connection.vendor != 'postgresql':
        return 'unproven'
    try:
        with transaction.atomic():
            _set_timeouts()
            session = ExperimentSession.objects.select_for_update(no_key=True, nowait=True).only(
                'id', 'status').get(pk=session_id, class_task_id=execution.task_id, student_id=execution.user_id)
            artifact = PreparedSessionArtifact.objects.values(*_ARTIFACT_FIELDS).get(session=session)
            if _matches(artifact, execution):
                return 'succeeded'
            existing = PreparedActivationCancellation.objects.filter(execution_id=execution.execution_id).first()
            if existing:
                return ('failed' if existing.session_id == session.pk
                        and existing.admission_token == execution.token else 'unproven')
            prepared = session.status == ExperimentSession.Status.PREPARED and not artifact['activation_receipt']
            different_winner = (_committed_receipt(artifact)
                                and str(artifact['activation_execution_id']) != execution.execution_id)
            if not prepared and not different_winner:
                return 'unproven'
            # This record fences the claim-to-transaction gap. Activation checks
            # it under the same session lock before writing state or timers.
            PreparedActivationCancellation.objects.create(
                execution_id=execution.execution_id, session=session, admission_token=execution.token)
            return 'failed'
    except (ExperimentSession.DoesNotExist, PreparedSessionArtifact.DoesNotExist):
        return 'missing'
    except DatabaseError as error:
        sqlstate = getattr(error.__cause__, 'sqlstate', None)
        if sqlstate in {'55P03', '57014'}:
            return 'busy'
        raise


def reconcile_execution(execution: AdmissionExecution, *, coordinator=None) -> str:
    """Release after receipt proof, or overdue durable cancellation commits.

    Obtaining a row lock alone is insufficient: a claimed worker can be paused
    before entering PostgreSQL. The persistent cancellation is essential.
    """
    if connection.in_atomic_block:
        raise AdmissionUnavailable('Reconciliation must run outside a database transaction')
    coordinator = coordinator or AdmissionCoordinator()
    try:
        with transaction.atomic():
            _set_timeouts()
            artifact = PreparedSessionArtifact.objects.values(*_ARTIFACT_FIELDS).get(
                session__class_task_id=execution.task_id, session__student_id=execution.user_id)
    except PreparedSessionArtifact.DoesNotExist:
        return 'missing'
    if _matches(artifact, execution):
        outcome = 'succeeded'
    elif not execution.recovery_due:
        return 'pending'
    else:
        outcome = _cancel_under_lock(execution, artifact['session_id'])
    if outcome in {'succeeded', 'failed'}:
        # All PostgreSQL work and any cancellation commit have finished here.
        coordinator.recover(task_id=execution.task_id, user_id=execution.user_id, action=execution.action,
                            token=execution.token, execution_id=execution.execution_id,
                            success=outcome == 'succeeded')
    return outcome


def reconcile_admission_page(*, cursor=0, limit=64, coordinator=None):
    """One bounded page; caller persists cursor or repeats a bounded command."""
    coordinator = coordinator or AdmissionCoordinator()
    next_cursor, executions = coordinator.executing_page(cursor=cursor, limit=limit)
    counts = Counter()
    for execution in executions:
        try:
            counts[reconcile_execution(execution, coordinator=coordinator)] += 1
        except AdmissionInvalid:
            # A concurrent reconciler or a newer fenced lifecycle won. Never
            # retry with an unverified replacement execution id.
            counts['changed'] += 1
    return {'cursor': next_cursor, 'examined': len(executions), 'outcomes': dict(counts),
            'occupancy': coordinator.occupancy()}
