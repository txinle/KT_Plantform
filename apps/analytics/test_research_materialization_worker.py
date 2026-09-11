from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase

from apps.analytics.management.commands.run_research_materializations import Command


class ResearchMaterializationWorkerCommandTests(SimpleTestCase):
    @patch("apps.analytics.management.commands.run_research_materializations.connection")
    def test_postgresql_uses_single_worker_advisory_lock(self, connection):
        connection.vendor = "postgresql"
        cursor = MagicMock()
        cursor.fetchone.return_value = (True,)
        connection.cursor.return_value.__enter__.return_value = cursor
        command = Command()

        self.assertTrue(command.try_acquire_lock())
        command.release_lock()

        self.assertEqual(
            cursor.execute.call_args_list,
            [
                call("SELECT pg_try_advisory_lock(%s)", [command.lock_id]),
                call("SELECT pg_advisory_unlock(%s)", [command.lock_id]),
            ],
        )

    @patch("apps.analytics.management.commands.run_research_materializations.materialization_metrics")
    @patch("apps.analytics.management.commands.run_research_materializations.run_session_materialization")
    @patch("apps.analytics.management.commands.run_research_materializations.claim_session_materialization")
    def test_one_cycle_claims_at_most_one_job(self, claim_job, run_job, metrics):
        job = MagicMock()
        claim_job.return_value = job
        metrics.return_value = {"backlog_count": 0}

        processed = Command().run_once(worker_id="worker")

        self.assertTrue(processed)
        claim_job.assert_called_once_with(worker_id="worker", lease_seconds=900)
        run_job.assert_called_once_with(job, worker_id="worker")

    @patch("apps.analytics.management.commands.run_research_materializations.close_old_connections")
    @patch("apps.analytics.management.commands.run_research_materializations.renew_session_materialization_lease")
    def test_running_job_renews_its_lease(self, renew_lease, close_connections):
        command = Command()
        command.heartbeat_interval = 1
        job = MagicMock()
        stop = MagicMock()
        stop.wait.side_effect = [False, True]
        renew_lease.return_value = job

        command._renew_lease_until_stopped(
            item=job, worker_id="worker", stop=stop
        )

        renew_lease.assert_called_once_with(
            job, worker_id="worker", lease_seconds=command.lease_seconds
        )
        self.assertEqual(close_connections.call_count, 2)
