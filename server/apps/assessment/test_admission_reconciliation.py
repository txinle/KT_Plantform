import uuid
import os
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from django.db import connection, connections, transaction
from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .admission import AdmissionCoordinator, AdmissionExecution, AdmissionUnavailable
from .models import ExperimentSession, PreparedActivationCancellation, PreparedSessionArtifact
from .test_student_api import StudentApiFixtureMixin


class AdmissionReconciliationTests(StudentApiFixtureMixin, TransactionTestCase):
    prepared_paper_fixture = True
    def setUp(self):
        super().setUp()
        self.session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.artifact, _ = PreparedSessionArtifact.objects.update_or_create(
            session=self.session, defaults={'preparation_version': 'test', 'ready': True})
        self.execution = AdmissionExecution(str(self.task.pk), str(self.student.pk), 'start', 'permit', str(uuid.uuid4()), True)
        self.coordinator = MagicMock()
        self.coordinator.recover.side_effect = lambda **identity: self.assertFalse(connection.in_atomic_block)

    def reconcile(self, execution=None):
        from .admission_reconciliation import reconcile_execution
        return reconcile_execution(execution or self.execution, coordinator=self.coordinator)

    def test_durable_matching_receipt_recovers_success_outside_transaction(self):
        self.session.status = ExperimentSession.Status.IN_PROGRESS
        self.session.started_at = timezone.now()
        self.session.save(update_fields=['status', 'started_at'])
        self.artifact.activation_execution_id = uuid.UUID(self.execution.execution_id)
        self.artifact.admission_token = self.execution.token
        self.artifact.activation_receipt = {'session_id': self.session.pk,
                                            'admission_execution_id': self.execution.execution_id}
        self.artifact.save()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.reconcile(), 'succeeded')
        self.assertTrue(any("set_config('statement_timeout'" in query['sql'] for query in queries))
        self.coordinator.recover.assert_called_once_with(task_id=self.execution.task_id, user_id=self.execution.user_id,
                                                        action='start', token='permit', execution_id=self.execution.execution_id,
                                                        success=True)
        self.assertFalse(PreparedActivationCancellation.objects.exists())

    def test_overdue_prepared_execution_gets_durable_cancellation_before_release(self):
        def recover(**identity):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(PreparedActivationCancellation.objects.filter(
                execution_id=self.execution.execution_id, session=self.session, admission_token='permit').exists())
        self.coordinator.recover.side_effect = recover
        self.assertEqual(self.reconcile(), 'failed')
        self.coordinator.recover.assert_called_once()
        self.assertEqual(self.reconcile(), 'failed')
        self.assertEqual(PreparedActivationCancellation.objects.count(), 1)

    def test_late_claimed_worker_cannot_activate_after_durable_cancellation(self):
        from .prepared_services import activate_prepared_session
        self.assertEqual(self.reconcile(), 'failed')
        with self.assertRaisesMessage(ValidationError, 'ACTIVATION_EXECUTION_CANCELLED'):
            activate_prepared_session(student=self.student, task_id=self.task.pk,
                                      client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4(),
                                      execution_id=self.execution.execution_id, admission_token='permit')
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ExperimentSession.Status.PREPARED)
        self.assertIsNone(self.session.started_at)

    def test_non_overdue_execution_is_left_occupied(self):
        fresh = AdmissionExecution(str(self.task.pk), str(self.student.pk), 'start', 'permit', str(uuid.uuid4()), False)
        self.assertEqual(self.reconcile(fresh), 'pending')
        self.coordinator.recover.assert_not_called()
        self.assertFalse(PreparedActivationCancellation.objects.exists())

    def test_wrong_receipt_is_not_treated_as_success(self):
        self.session.status = ExperimentSession.Status.IN_PROGRESS
        self.session.started_at = timezone.now()
        self.session.save(update_fields=['status', 'started_at'])
        self.artifact.activation_receipt = {'session_id': self.session.pk}
        self.artifact.save()
        self.assertEqual(self.reconcile(), 'unproven')
        self.coordinator.recover.assert_not_called()

    def test_different_committed_winner_can_cancel_old_execution_without_changing_receipt(self):
        self.session.status = ExperimentSession.Status.IN_PROGRESS
        self.session.started_at = timezone.now()
        self.session.save(update_fields=['status', 'started_at'])
        winner_id = uuid.uuid4()
        receipt = {'session_id': self.session.pk, 'admission_execution_id': str(winner_id)}
        self.artifact.activation_execution_id = winner_id
        self.artifact.admission_token = 'winner-token'
        self.artifact.activation_receipt = receipt
        self.artifact.save()
        self.assertEqual(self.reconcile(), 'failed')
        self.artifact.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.artifact.activation_receipt, receipt)
        self.assertEqual(self.session.status, ExperimentSession.Status.IN_PROGRESS)
        self.assertTrue(PreparedActivationCancellation.objects.filter(execution_id=self.execution.execution_id).exists())

    def test_ongoing_postgres_activation_lock_prevents_recovery(self):
        if connection.vendor != 'postgresql':
            self.skipTest('requires PostgreSQL row-lock semantics')
        from .admission_reconciliation import reconcile_execution
        def reconcile_in_worker():
            connections.close_all()
            try:
                return reconcile_execution(self.execution, coordinator=self.coordinator)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                ExperimentSession.objects.select_for_update(no_key=True).get(pk=self.session.pk)
                self.assertEqual(pool.submit(reconcile_in_worker).result(timeout=5), 'busy')
        self.coordinator.recover.assert_not_called()
        self.assertFalse(PreparedActivationCancellation.objects.exists())

    def test_redis_failure_keeps_the_durable_cancellation_for_retry(self):
        self.coordinator.recover.side_effect = AdmissionUnavailable
        with self.assertRaises(AdmissionUnavailable):
            self.reconcile()
        self.assertTrue(PreparedActivationCancellation.objects.filter(execution_id=self.execution.execution_id).exists())

    @skipUnless(os.environ.get('ADMISSION_TEST_REDIS_URL'), 'requires isolated admission Redis')
    def test_postgres_cancellation_releases_real_execution_before_next_acquisition(self):
        import redis
        from .admission_reconciliation import reconcile_admission_page
        client = redis.Redis.from_url(os.environ['ADMISSION_TEST_REDIS_URL'], decode_responses=True)
        prefix = f'assessment:admission:test-reconcile-{uuid.uuid4()}'
        def cleanup():
            keys = list(client.scan_iter(match=f'{prefix}:*'))
            if keys:
                client.delete(*keys)
        self.addCleanup(cleanup)
        with override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True, ASSESSMENT_ADMISSION_ENABLED=True,
                               ASSESSMENT_ADMISSION_KEY_PREFIX=prefix, ASSESSMENT_ADMISSION_CAPACITY=1,
                               ASSESSMENT_ADMISSION_RATE_PER_SECOND=1000000,
                               ASSESSMENT_ADMISSION_RECONCILIATION_GRACE_SECONDS=0):
            coordinator = AdmissionCoordinator(client)
            first = coordinator.request(task_id=self.task.pk, user_id=self.student.pk, action='start')
            second = coordinator.request(task_id=self.task.pk, user_id=self.peer.pk, action='start')
            self.assertEqual(second.status, 'busy')
            client.hset(prefix + ':token:' + first.token, 'lease_until', 0)
            result = reconcile_admission_page(limit=1, coordinator=coordinator)
            self.assertEqual(result['outcomes'], {'failed': 1})
            self.assertEqual(result['occupancy'], {'admitted': 0, 'executing': 0, 'occupied': 0})
            self.assertTrue(PreparedActivationCancellation.objects.filter(execution_id=first.execution_id,
                                                                           admission_token=first.token).exists())
            self.assertTrue(coordinator.request(task_id=self.task.pk, user_id=self.peer.pk, action='start').execution_claimed)
