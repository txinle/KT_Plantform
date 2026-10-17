from io import StringIO
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase, override_settings

from apps.api.management.commands.run_scheduler import Command


class SchedulerCommandTests(SimpleTestCase):
    @override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=False)
    @patch('apps.api.management.commands.run_scheduler.time.monotonic',
           side_effect=[10, 10.25, 20, 20.25, 30, 30.25, 40, 40.25, 50, 50.25])
    @patch('apps.api.management.commands.run_scheduler.call_command')
    def test_logs_one_bounded_duration_record_per_phase(self, call_command, monotonic):
        output = StringIO()
        Command(stdout=output).run_once()
        records = output.getvalue().splitlines()
        self.assertEqual(len(records), 5)
        for record, phase in zip(records, [item.args[0] for item in call_command.call_args_list]):
            self.assertEqual(record,
                             f'scheduler_phase name={phase} status=completed duration_ms=250.000')

    @override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=False)
    @patch('apps.api.management.commands.run_scheduler.call_command',
           side_effect=RuntimeError('private student and connection details'))
    def test_failed_phase_is_timed_without_exposing_exception_and_releases_lock(self, call_command):
        output = StringIO()
        command = Command(stdout=output)
        with patch.object(command, 'try_acquire_lock', return_value=True), patch.object(
            command, 'release_lock'
        ) as release:
            with self.assertRaises(RuntimeError):
                command.handle(once=True, interval=30)
        release.assert_called_once_with()
        call_command.assert_called_once_with('expire_publish_plans')
        records = output.getvalue().splitlines()
        self.assertEqual(len(records), 1)
        self.assertIn('name=expire_publish_plans status=failed duration_ms=', records[0])
        self.assertNotIn('private student', records[0])

    @override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True, ASSESSMENT_PREPARED_ADMISSION_MODE='redis')
    @patch('apps.assessment.admission_reconciliation.reconcile_admission_page')
    @patch('apps.api.management.commands.run_scheduler.call_command')
    def test_global_admission_reconciles_one_bounded_page_each_cycle(self, call_command, reconcile):
        reconcile.return_value = {'cursor': 64}
        command = Command()
        command.run_once()
        reconcile.assert_called_once_with(cursor=0, limit=64)
        self.assertEqual(command._admission_cursor, 64)

    @override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True,
                       ASSESSMENT_PREPARED_ADMISSION_MODE='postgres_transaction')
    @patch('apps.assessment.admission_reconciliation.reconcile_admission_page')
    @patch('apps.api.management.commands.run_scheduler.call_command')
    def test_transaction_admission_skips_redis_reconciliation_and_keeps_background_jobs(self, call_command, reconcile):
        Command().run_once()
        reconcile.assert_not_called()
        self.assertEqual(call_command.call_args_list, [
            call('expire_publish_plans'), call('expire_due_sessions'),
            call('run_student_import_jobs'), call('reconcile_research_materializations'),
            call('run_export_jobs'),
        ])

    @patch("apps.api.management.commands.run_scheduler.call_command")
    def test_one_cycle_runs_only_the_idempotent_scheduler_jobs(self, call_command):
        Command().run_once()

        self.assertEqual(
            call_command.call_args_list,
            [
                call("expire_publish_plans"),
                call("expire_due_sessions"),
                call("run_student_import_jobs"),
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
