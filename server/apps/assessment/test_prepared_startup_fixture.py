import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext

from .test_student_api import StudentApiFixtureMixin


class PreparedFixtureSourceHashTests(SimpleTestCase):
    def test_server_only_layout_uses_base_dir_and_preserves_repository_hash_keys(self):
        from .management.commands.seed_prepared_startup_load import _source_hashes
        sources = ('server/apps/assessment/management/commands/seed_prepared_startup_load.py',
                   'server/apps/assessment/prepared_services.py', 'server/apps/assessment/prepared_content.py',
                   'server/apps/experiments/publication_services.py', 'server/apps/assessment/admission.py')
        with tempfile.TemporaryDirectory() as temporary:
            container_root = Path(temporary)
            app = container_root / 'app'
            expected = {}
            for source in sources:
                payload = (source + '\r\n原始字节\r\n').encode('utf-8')
                path = app / Path(source).relative_to('server')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                expected[source] = hashlib.sha256(payload).hexdigest()
            with override_settings(BASE_DIR=app, REPOSITORY_DIR=container_root):
                self.assertEqual(_source_hashes(), expected)

    def test_missing_server_source_is_an_explicit_failure(self):
        from .management.commands.seed_prepared_startup_load import _source_hashes
        with tempfile.TemporaryDirectory() as temporary, override_settings(BASE_DIR=Path(temporary)):
            with self.assertRaises(FileNotFoundError):
                _source_hashes()


@override_settings(ASSESSMENT_PREPARED_ENABLED=True, ASSESSMENT_ADMISSION_ENABLED=True,
                   ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
class PreparedFixtureGuardTests(SimpleTestCase):
    @override_settings(SCHOOL_POOL_PROFILE='shared-entry-v1', ASSESSMENT_ADMISSION_CAPACITY=8,
                       ASSESSMENT_ENTRY_WORKERS=11, ASSESSMENT_ENTRY_DB_CONNECTIONS=11,
                       ASSESSMENT_ENTRY_STARTUP_LIMIT=8, ASSESSMENT_STARTUP_WORKERS=2,
                       ASSESSMENT_STARTUP_DB_CONNECTIONS=2)
    def test_shared_fixture_guard_uses_the_running_entry_budget(self):
        from .management.commands.seed_prepared_startup_load import Command
        module = 'apps.assessment.management.commands.seed_prepared_startup_load.'
        with tempfile.TemporaryDirectory() as directory, patch(module + 'connection') as db, \
                patch(module + 'User') as users, patch(module + 'Course') as courses, \
                patch(module + '_private_stream', side_effect=RuntimeError('capacity guard passed')):
            db.settings_dict = {'NAME': 'kt_e2e'}
            db.vendor = 'postgresql'
            users.objects.filter.return_value.exists.return_value = False
            courses.objects.filter.return_value.exists.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'capacity guard passed'):
                Command().handle(run_id='shared-guard', scenario='same-task', questions=20,
                                 output=str(Path(directory) / 'fixture.json'))

    def test_production_database_is_rejected_before_any_write(self):
        from .management.commands.seed_prepared_startup_load import Command
        for database in ('kt_platform', 'school-production', 'kt_e2e_backup', 'sqlite'):
            with self.subTest(database=database), patch('apps.assessment.management.commands.seed_prepared_startup_load.connection') as db:
                db.settings_dict = {'NAME': database}
                db.vendor = 'postgresql'
                with self.assertRaises(CommandError):
                    Command().handle(run_id='guard', scenario='same-task', questions=20, output='unused.json')

    @override_settings(ASSESSMENT_PREPARED_ENABLED=False)
    def test_disabled_prepared_mode_is_rejected_before_writes(self):
        from .management.commands.seed_prepared_startup_load import Command
        with patch('apps.assessment.management.commands.seed_prepared_startup_load.connection') as db:
            db.settings_dict = {'NAME': 'test_fixture'}
            db.vendor = 'postgresql'
            with self.assertRaises(CommandError):
                Command().handle(run_id='guard', scenario='same-task', questions=20, output='unused.json')

    def test_scenarios_have_180_fixed_students_and_mixed_only_60_active(self):
        from .management.commands.seed_prepared_startup_load import fixture_layout
        same = fixture_layout('same-task')
        three = fixture_layout('three-task')
        mixed = fixture_layout('mixed')
        self.assertEqual(len(same), 180)
        self.assertEqual({r['task_index'] for r in same}, {0})
        self.assertEqual([sum(r['task_index'] == n for r in three) for n in range(3)], [60, 60, 60])
        self.assertEqual(sum(r['active'] for r in mixed), 60)

    def test_explicit_headroom_cohorts_preserve_proportions_and_reject_unbounded_counts(self):
        from .management.commands.seed_prepared_startup_load import fixture_layout
        for count in (240, 360):
            mixed = fixture_layout('mixed', students=count)
            self.assertEqual(len(mixed), count)
            self.assertEqual(sum(row['active'] for row in mixed), count // 3)
            three = fixture_layout('three-task', students=count)
            self.assertEqual([sum(row['task_index'] == n for row in three) for n in range(3)], [count // 3] * 3)
        for count in (0, 500, -1):
            with self.assertRaises(CommandError):
                fixture_layout('same-task', students=count)


@override_settings(ASSESSMENT_PREPARED_ENABLED=True)
class PreparedFixtureExportTests(StudentApiFixtureMixin, TestCase):
    prepared_paper_fixture = True
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.paper_settings = override_settings(ASSESSMENT_PAPER_ROOT=Path(self.directory.name))
        self.paper_settings.enable()
        self.addCleanup(self.paper_settings.disable)
        super().setUp()

    def test_cohort_export_reads_only_required_fields_in_two_queries(self):
        from .management.commands.seed_prepared_startup_load import Command
        layout = [{'index': n, 'task_index': 0, 'active': False} for n in range(2)]
        tasks = [{'task_id': self.task.pk, 'course_id': self.course.pk}]
        with CaptureQueriesContext(connection) as queries:
            accounts = Command()._export_accounts('export', layout, tasks, [self.student, self.peer])
        self.assertEqual(len(accounts), 2)
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(len(a['questions']) == 1 for a in accounts))
        self.assertFalse(any('"runtime"' in q['sql'] or '"correct_answer"' in q['sql'] for q in queries))


@override_settings(ASSESSMENT_PREPARED_ENABLED=True, ASSESSMENT_ADMISSION_ENABLED=True,
                   ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
class PreparedStartupFixtureTests(TransactionTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.paper_settings = override_settings(ASSESSMENT_PAPER_ROOT=self.path / 'papers')
        self.paper_settings.enable()
        self.addCleanup(self.paper_settings.disable)

    def seed(self, run, scenario):
        output = self.path / f'{run}.json'
        call_command('seed_prepared_startup_load', run_id=run, scenario=scenario, questions=20, output=str(output))
        return json.loads(output.read_text(encoding='utf-8'))

    def test_three_task_fixture_uses_actual_publication_and_stays_unstarted(self):
        from .models import AssessmentEvent, ConfidenceAssignment, ExperimentSession, PreparedSessionArtifact
        fixture = self.seed('three-task-test', 'three-task')
        self.assertTrue(fixture['isolated_demo'])
        self.assertEqual(len(fixture['accounts']), 180)
        task_ids = {a['taskId'] for a in fixture['accounts']}
        self.assertEqual(len(task_ids), 3)
        self.assertEqual([sum(a['taskId'] == task for a in fixture['accounts']) for task in task_ids], [60, 60, 60])
        self.assertEqual(PreparedSessionArtifact.objects.filter(session__class_task_id__in=task_ids, ready=True).count(), 180)
        sessions = ExperimentSession.objects.filter(class_task_id__in=task_ids)
        self.assertEqual(set(sessions.values_list('status', flat=True)), {'prepared'})
        self.assertFalse(sessions.exclude(started_at=None).exists())
        self.assertFalse(AssessmentEvent.objects.filter(session__class_task_id__in=task_ids).exists())
        self.assertEqual(ConfidenceAssignment.objects.filter(attempt__session__class_task_id__in=task_ids).count(), 3600)
        for account in fixture['accounts']:
            self.assertEqual(len(account['questions']), 20)
            self.assertEqual(account['password'], 'PreparedFixture!test-only')
        for task in fixture['tasks']:
            self.assertEqual(task['confidence_mode'], 'sampled')
            self.assertEqual(task['confidence_scale_version'], 'v2')
        material = fixture['fixture_spec']
        encoded = json.dumps(material, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), fixture['plan_sha'])
        with self.assertRaises(CommandError):
            self.seed('three-task-test', 'three-task')

    def test_mixed_fixture_really_activates_only_first_60(self):
        from .models import AssessmentEvent, ExperimentSession
        fixture = self.seed('mixed-test', 'mixed')
        active = [a for a in fixture['accounts'] if a['initial_state'] == 'active']
        newcomers = [a for a in fixture['accounts'] if a['initial_state'] == 'prepared']
        self.assertEqual((len(active), len(newcomers)), (60, 120))
        sessions = ExperimentSession.objects.filter(class_task_id=fixture['tasks'][0]['task_id'])
        self.assertEqual(sessions.filter(status='in_progress').count(), 60)
        self.assertEqual(sessions.filter(status='prepared', started_at=None).count(), 120)
        self.assertEqual(AssessmentEvent.objects.filter(session__in=sessions, event_type='session_started').count(), 60)
        self.assertEqual(AssessmentEvent.objects.filter(session__in=sessions, event_type='item_entered').count(), 60)
        self.assertTrue(all(a['question_url'] and a['save_value'] == 'A' for a in active))
