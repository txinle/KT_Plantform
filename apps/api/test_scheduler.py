from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase

from apps.api.management.commands.run_scheduler import Command


class SchedulerCommandTests(SimpleTestCase):
    @patch("apps.api.management.commands.run_scheduler.call_command")
    def test_one_cycle_runs_only_the_idempotent_scheduler_jobs(self, call_command):
        Command().run_once()

        self.assertEqual(
            call_command.call_args_list,
            [
                call("expire_publish_plans"),
                call("expire_due_sessions"),
                call("reconcile_research_materializations"),
                call("run_export_jobs"),
            ],
        )

    @patch("apps.api.management.commands.run_scheduler.connection")
    def test_postgresql_uses_one_advisory_lock(self, connection):
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
