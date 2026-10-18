import json
from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless
from uuid import uuid4

from django.test import RequestFactory, TestCase, TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.api.test_support import domain_payload
from . import test_research_deletion as fixtures


class DeletionOperationTests(TestCase):
    setUp = fixtures.ResearchDeletionTests.setUp
    publish = fixtures.ResearchDeletionTests.publish
    url = fixtures.ResearchDeletionTests.url
    preview = fixtures.ResearchDeletionTests.preview

    def operation_url(self, operation_id=None):
        base = f'/api/v1/teacher/courses/{self.course.pk}/deletion-operations/'
        return base + (f'{operation_id}/' if operation_id else '')

    def admit(self, operation_id=None, preview=None):
        preview = preview or self.preview(self.url())
        operation_id = operation_id or uuid4()
        response = self.client.post(self.operation_url(), json.dumps({
            'operation_id': str(operation_id), 'action': 'delete_experiment',
            'object_id': self.experiment.pk, 'class_id': None,
            'confirmation_token': preview['confirmation_token'],
        }), content_type='application/json')
        self.assertIn(response.status_code, (200, 201), response.content)
        return operation_id, preview

    def execute(self, operation_id, preview):
        return self.client.post(self.url(), json.dumps({'confirmation': 'delete',
            'confirmation_token': preview['confirmation_token'], 'operation_id': str(operation_id)}),
            content_type='application/json')

    def test_receipt_survives_delete_and_replay_does_not_lookup_deleted_target(self):
        operation_id, preview = self.admit()
        response = self.execute(operation_id, preview)
        self.assertEqual(response.status_code, 200, response.content)
        again = self.execute(operation_id, preview)
        self.assertEqual(again.status_code, 200, again.content)
        status = domain_payload(self.client.get(self.operation_url(operation_id)))
        self.assertEqual(status['state'], 'succeeded')
        self.assertTrue(status['result']['deleted'])

    def test_admission_is_idempotent_and_does_not_delete(self):
        from .models import Experiment, ResearchDeletionOperation
        operation_id, preview = self.admit()
        self.admit(operation_id, preview)
        self.assertEqual(ResearchDeletionOperation.objects.filter(pk=operation_id).count(), 1)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())

    def test_expired_unstarted_operation_is_rejected_and_late_post_cannot_delete(self):
        from .deletion_operations import recover_pending_deletion_operations
        from .models import Experiment, ResearchDeletionOperation
        operation_id, preview = self.admit()
        ResearchDeletionOperation.objects.filter(pk=operation_id).update(execute_before=timezone.now() - timedelta(seconds=1))
        self.assertEqual(recover_pending_deletion_operations(), 1)
        self.assertEqual(self.execute(operation_id, preview).status_code, 409)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())

    def test_missing_operation_id_is_rejected_before_deletion(self):
        response = self.client.post(self.url(), json.dumps({'confirmation': 'delete',
            'confirmation_token': self.preview(self.url())['confirmation_token']}), content_type='application/json')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['error']['code'], 'DELETION_PREVIEW_REQUIRED')

    def test_unexpected_failure_rolls_back_tree_and_leaves_pending_receipt(self):
        from .models import Experiment, ResearchDeletionOperation
        operation_id, preview = self.admit()
        with patch('apps.experiments.research_deletion.log_action', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.execute(operation_id, preview)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertEqual(ResearchDeletionOperation.objects.get(pk=operation_id).state, 'pending')

    def test_other_teacher_cannot_query_receipt(self):
        from apps.accounts.models import User
        operation_id, _ = self.admit()
        other = User.objects.create_user(username='receipt-other', role=User.Role.TEACHER)
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.operation_url(operation_id)).status_code, 404)

    def test_same_operation_cannot_change_confirmation_material(self):
        operation_id, preview = self.admit()
        response = self.client.post(self.operation_url(), json.dumps({
            'operation_id': str(operation_id), 'action': 'delete_experiment', 'object_id': self.experiment.pk,
            'class_id': None, 'confirmation_token': preview['confirmation_token'] + 'changed',
        }), content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'DELETION_IDEMPOTENCY_CONFLICT')

    def test_scope_change_after_admission_records_rejection_without_deleting(self):
        from .models import ResearchDeletionOperation
        from apps.analytics.models import ExportJob, ExportSnapshot
        operation_id, preview = self.admit()
        snapshot = ExportSnapshot.objects.create(course=self.course, created_by=self.teacher,
            selected_versions=[{'id': self.experiment.versions.first().pk}], source_bundle={})
        ExportJob.objects.create(course=self.course, snapshot=snapshot, created_by=self.teacher)
        response = self.execute(operation_id, preview)
        self.assertEqual(response.status_code, 409)
        receipt = ResearchDeletionOperation.objects.get(pk=operation_id)
        self.assertEqual(receipt.state, 'rejected')
        self.assertEqual(receipt.error_code, 'DELETION_SCOPE_CHANGED')


class DeletionOperationCommitTests(TransactionTestCase):
    setUp = fixtures.PreparedResearchDeletionCommitTests.setUp

    def registered(self):
        from .deletion_operations import register_deletion_operation
        from .research_deletion import deletion_preview
        token = deletion_preview(course=self.course, actor=self.teacher, action='delete_experiment', experiment=self.experiment)['confirmation_token']
        operation, _ = register_deletion_operation(course=self.course, actor=self.teacher, operation_id=uuid4(),
            action='delete_experiment', object_id=self.experiment.pk, class_id=None, confirmation_token=token)
        return operation, token

    def execute(self, operation, token):
        from .deletion_operations import execute_registered_deletion
        request = RequestFactory().post('/')
        request.user = self.teacher
        return execute_registered_deletion(request=request, course=self.course, operation_id=operation.pk,
            action='delete_experiment', object_id=self.experiment.pk, class_id=None, confirmation_token=token)

    def test_commit_failure_rolls_back_deletion_and_receipt_together(self):
        from django.db import OperationalError, connection
        from .models import Experiment
        operation, token = self.registered()
        with patch.object(connection, '_commit', side_effect=OperationalError('commit unavailable')):
            with self.assertRaises(OperationalError):
                self.execute(operation, token)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'pending')
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())

    def test_commit_succeeded_but_response_lost_is_queryable_as_succeeded(self):
        from django.db import OperationalError, connection
        from .models import Experiment
        operation, token = self.registered()
        original = connection._commit
        def committed_but_disconnected():
            original()
            raise OperationalError('connection lost after commit')
        with patch.object(connection, '_commit', side_effect=committed_but_disconnected):
            with self.assertRaises(OperationalError):
                self.execute(operation, token)
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'succeeded')

        self.assertTrue(operation.result['deleted'])
        self.assertFalse(Experiment.objects.filter(pk=self.experiment.pk).exists())

    def test_recovery_skips_transaction_currently_executing_deletion(self):
        from queue import Queue
        from threading import Event, Thread
        from django.db import close_old_connections
        from . import research_deletion
        from .deletion_operations import recover_pending_deletion_operations
        operation, token = self.registered()
        entered, release = Event(), Event()
        errors = Queue()
        original = research_deletion._purge_sessions
        def paused(*args):
            entered.set()
            if not release.wait(10):
                raise TimeoutError('release deletion')
            return original(*args)
        def execute():
            close_old_connections()
            try:
                self.execute(operation, token)
            except Exception as error:
                errors.put(error)
            finally:
                close_old_connections()
        with patch.object(research_deletion, '_purge_sessions', side_effect=paused):
            thread = Thread(target=execute)
            thread.start()
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(recover_pending_deletion_operations(now=timezone.now() + timedelta(minutes=1)), 0)
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors.empty(), list(errors.queue))
        operation.refresh_from_db()
        self.assertEqual(operation.state, 'succeeded')


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL deletion receipt migration')
class DeletionOperationMigrationTests(TransactionTestCase):
    def test_reverse_migration_refuses_to_drop_pending_receipts(self):
        from .models import ResearchDeletionOperation
        fixtures.publication_fixtures.APlusPreviewAndCommitTests.setUp(self)
        operation = ResearchDeletionOperation.objects.create(
            operation_id=uuid4(), actor=self.teacher, course=self.course,
            action='delete_experiment', object_id=self.experiment.pk,
            scope_fingerprint='f' * 64, frozen_scope={}, confirmation_token_hash='a' * 64,
            execute_before=timezone.now() - timedelta(seconds=1),
        )
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            with self.assertRaisesMessage(RuntimeError, '删除回执尚未核实'):
                MigrationExecutor(connection).migrate([('experiments', '0032_student_comparison')])
            self.assertTrue(ResearchDeletionOperation.objects.filter(pk=operation.pk, state='pending').exists())
        finally:
            MigrationExecutor(connection).migrate(latest)

    def test_reverse_migration_allows_empty_or_terminal_receipts(self):
        from .models import ResearchDeletionOperation
        fixtures.publication_fixtures.APlusPreviewAndCommitTests.setUp(self)
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            for state in (None, 'succeeded', 'rejected'):
                with self.subTest(state=state):
                    if state:
                        ResearchDeletionOperation.objects.create(
                            operation_id=uuid4(), actor=self.teacher, course=self.course,
                            action='delete_experiment', object_id=self.experiment.pk, state=state,
                            scope_fingerprint='f' * 64, frozen_scope={}, confirmation_token_hash='a' * 64,
                            execute_before=timezone.now(), finished_at=timezone.now(),
                        )
                    MigrationExecutor(connection).migrate([('experiments', '0032_student_comparison')])
                    self.assertNotIn(ResearchDeletionOperation._meta.db_table, connection.introspection.table_names())
                    MigrationExecutor(connection).migrate(latest)
        finally:
            MigrationExecutor(connection).migrate(latest)
