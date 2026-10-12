import importlib.util
import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class SchoolSchedulerDiagnosticTests(SimpleTestCase):
    def module(self):
        path = Path(settings.BASE_DIR) / 'scripts/ops/school_scheduler_diagnostic.py'
        self.assertTrue(path.exists(), 'Read-only scheduler diagnostic missing')
        spec = importlib.util.spec_from_file_location('school_scheduler_diagnostic', path)
        result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
        return result

    def test_known_stdout_marker_becomes_boolean_without_secret_log_content(self):
        helper = self.module()
        self.assertEqual(helper.log_metadata('password=private\nscheduler lock is held by another process\n'), {'known_lock_held_message': True})
        self.assertEqual(helper.log_metadata('some dynamic lock message private'), {'known_lock_held_message': False})

    def test_command_and_environment_are_reduced_to_fixed_safe_values(self):
        helper = self.module()
        item = {'Id': 'a' * 64, 'RestartCount': 1, 'State': {'Pid': 0, 'Running': False, 'Restarting': False, 'ExitCode': 0},
            'Config': {'Labels': {'com.docker.compose.project': 'kt-school-e2e', 'com.docker.compose.service': 'scheduler'},
                'Cmd': ['python', 'manage.py', 'run_scheduler', '--once', '--password=private'],
                'Env': ['POSTGRES_DB=kt_e2e', 'POSTGRES_HOST=school-test-postgres', 'POSTGRES_POOL_ALIAS=', 'POSTGRES_PASSWORD=private', 'DATABASE_APPLICATION_NAME=kt-school-scheduler-direct']}}
        value = helper.scheduler_metadata(item)
        self.assertTrue(value['runs_scheduler']); self.assertTrue(value['has_once'])
        self.assertEqual(value['database'], 'kt_e2e'); self.assertEqual(value['host'], 'school-test-postgres')
        self.assertEqual(value['RestartCount'], 1)
        self.assertNotIn('private', json.dumps(value)); self.assertNotIn('Cmd', value)
        item['Config']['Cmd'] = ['sh', 'some dynamic command run_scheduler --once private']
        self.assertFalse(helper.scheduler_metadata(item)['runs_scheduler'])
        self.assertFalse(helper.scheduler_metadata(item)['has_once'])

    def test_lock_query_is_read_only_current_database_scoped_without_query_text(self):
        helper = self.module()
        sql = helper.LOCK_SQL.lower()
        self.assertTrue(sql.lstrip().startswith('select'))
        self.assertIn('current_database()', sql); self.assertIn('pg_locks', sql)
        self.assertNotIn('pg_try_advisory', sql); self.assertNotIn('pg_advisory_lock', sql)
        self.assertNotIn('pg_terminate', sql); self.assertNotIn('query', sql); self.assertNotIn('usename', sql)
        self.assertEqual((helper.LOCK_CLASSID << 32) + helper.LOCK_OBJID, 4_819_520_817)
        self.assertIn('SET TRANSACTION READ ONLY', helper.DB_CODE)
        self.assertLess(helper.DB_CODE.index('SET TRANSACTION READ ONLY'), helper.DB_CODE.index('SELECT current_database()'))
