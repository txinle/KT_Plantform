from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db import OperationalError
from django.db import connection
from django.test import SimpleTestCase

from apps.assessment.management.commands.run_session_submissions import Command


class SessionSubmissionWorkerCommandTests(SimpleTestCase):
    def test_pooled_route_cannot_report_worker_liveness_from_backend_tags(self):
        from .session_submission_services import session_submission_worker_status
        with patch.dict(connection.settings_dict, {'HOST': 'pgbouncer'}):
            self.assertEqual(session_submission_worker_status()['status'], 'unknown')
        with patch.dict('os.environ', {'POSTGRES_POOL_ALIAS': 'pooled-assessment'}):
            self.assertEqual(session_submission_worker_status()['status'], 'unknown')

    def test_pooled_worker_does_not_tag_or_retain_a_pool_backend(self):
        from .management.commands.run_session_submissions import mark_worker_connection
        pooled = Mock(vendor='postgresql', settings_dict={'HOST': 'pgbouncer'})
        original_close_at = pooled.close_at
        with patch('apps.assessment.management.commands.run_session_submissions.db.connection', pooled):
            mark_worker_connection()
        pooled.cursor.assert_not_called()
        self.assertIs(pooled.close_at, original_close_at)

    def test_status_reports_observation_without_claiming_work(self):
        output = StringIO()
        with patch('apps.assessment.management.commands.run_session_submissions.observe_submission_queue',
                   return_value={'queued_count': 2, 'oldest_queued_age_seconds': 45}), patch(
            'apps.assessment.management.commands.run_session_submissions.claim_session_submission'
        ) as claim:
            Command(stdout=output).handle(status=True)
        claim.assert_not_called()
        self.assertIn('"oldest_queued_age_seconds": 45', output.getvalue())

    def test_closes_old_connections_before_claim_and_after_processing(self):
        job = SimpleNamespace(pk='receipt', status='completed')
        order = Mock()
        with patch('django.db.close_old_connections', order.cleanup), patch(
            'apps.assessment.management.commands.run_session_submissions.claim_session_submission',
            order.claim,
        ), patch(
            'apps.assessment.management.commands.run_session_submissions.run_session_submission',
            order.process,
        ):
            order.claim.return_value = job
            order.process.return_value = job
            Command(stdout=StringIO()).handle(once=True, retry_failed=None, poll_seconds=2)
        self.assertEqual([item[0] for item in order.mock_calls],
                         ['cleanup', 'claim', 'process', 'cleanup'])

    def test_claim_connection_failure_reconnects_then_claims_again_without_replaying_job(self):
        job = SimpleNamespace(pk='receipt', status='completed')
        with patch('django.db.close_old_connections') as cleanup, patch(
            'django.db.connections.close_all'
        ) as disconnect, patch(
            'apps.assessment.management.commands.run_session_submissions.claim_session_submission',
            side_effect=[OperationalError('private connection details'), job, KeyboardInterrupt],
        ) as claim_job, patch(
            'apps.assessment.management.commands.run_session_submissions.run_session_submission',
            return_value=job,
        ) as process, patch(
            'apps.assessment.management.commands.run_session_submissions.time.sleep'
        ) as sleep, self.assertLogs(
            'apps.assessment.management.commands.run_session_submissions', level='WARNING'
        ) as logs:
            with self.assertRaises(KeyboardInterrupt):
                Command(stdout=StringIO()).handle(once=False, retry_failed=None, poll_seconds=2)
        disconnect.assert_called_once_with()
        process.assert_called_once_with(job)
        self.assertEqual(claim_job.call_count, 3)
        sleep.assert_called_once_with(2)
        self.assertEqual(cleanup.call_count, 6)
        self.assertNotIn('private connection details', '\n'.join(logs.output))

    def test_once_connection_failure_remains_visible_and_cleans_up(self):
        with patch('django.db.close_old_connections') as cleanup, patch(
            'django.db.connections.close_all'
        ), patch(
            'apps.assessment.management.commands.run_session_submissions.claim_session_submission',
            side_effect=OperationalError('database unavailable'),
        ), self.assertLogs(
            'apps.assessment.management.commands.run_session_submissions', level='WARNING'
        ):
            with self.assertRaises(OperationalError):
                Command(stdout=StringIO()).handle(once=True, retry_failed=None, poll_seconds=2)
        self.assertEqual(cleanup.call_count, 2)
