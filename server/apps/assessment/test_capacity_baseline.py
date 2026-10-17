"""The reusable baseline preserves source identities and creates fresh publication rows."""
import copy
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings


class CapacityBaselineGuardTests(SimpleTestCase):
    def test_database_and_explicit_isolation_are_required(self):
        from .management.commands.prepare_student_capacity_baseline import require_isolated_database
        for vendor, name, flag in [('sqlite', 'test_capacity', '1'), ('postgresql', 'kt_platform', '1'),
                                   ('postgresql', 'kt_e2e', '0'), ('postgresql', 'kt_e2e', '')]:
            with self.subTest(vendor=vendor, name=name, flag=flag), patch(
                    'apps.assessment.management.commands.prepare_student_capacity_baseline.connection') as db, \
                    patch.dict(os.environ, SCHOOL_RUNTIME_ISOLATION_ENABLED=flag):
                db.vendor, db.settings_dict = vendor, {'NAME': name}
                with self.assertRaises(CommandError):
                    require_isolated_database()


@override_settings(ASSESSMENT_PREPARED_ENABLED=True, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CapacityBaselineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .management.commands.seed_prepared_startup_load import Command, fixture_layout
        cls.directory = TemporaryDirectory()
        cls.paper_settings = override_settings(ASSESSMENT_PAPER_ROOT=Path(cls.directory.name) / 'papers')
        cls.paper_settings.enable()
        cls.addClassCleanup(cls.paper_settings.disable)
        cls.addClassCleanup(cls.directory.cleanup)
        cls.source = Command()._seed('source-baseline', fixture_layout('same-task'), 'same-task', 20, 'kt_e2e')

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source_path = Path(self.temp.name) / 'source.json'
        self.output = Path(self.temp.name) / 'baseline.json'
        self.source_path.write_text(json.dumps(self.source), encoding='utf-8')

    def prepare(self, **extra):
        with patch.dict(os.environ, SCHOOL_RUNTIME_ISOLATION_ENABLED='1'):
            call_command('prepare_student_capacity_baseline', source_fixture=str(self.source_path),
                         baseline_id='baseline-test', run_id='baseline-test', output=str(self.output),
                         stdout=io.StringIO(), **extra)
        return json.loads(self.output.read_text(encoding='utf-8'))

    def test_fresh_publication_reuses_users_and_twenty_questions_preserving_old_history(self):
        from apps.accounts.models import User
        from apps.experiments.models import Experiment, QuestionSnapshot
        from .models import Attempt, ExperimentSession
        old_sessions = list(ExperimentSession.objects.order_by('id').values())
        old_attempts = list(Attempt.objects.order_by('id').values())
        user_count = User.objects.count()
        baseline = self.prepare()
        self.assertEqual(User.objects.count(), user_count)
        self.assertEqual(baseline['source_fixture_id'], self.source['plan_sha'])
        self.assertEqual(baseline['baseline_snapshot_id'], 'baseline-test')
        self.assertEqual(baseline['source_ownership']['command'], 'prepare_student_capacity_baseline')
        self.assertEqual(baseline['teacher_account']['username'], 'pp-source-baseline-teacher')
        self.assertEqual(baseline['fixture_id'], baseline['plan_sha'])
        self.assertEqual(baseline['student_count'], 180)
        self.assertEqual(len(baseline['account_mapping']), 180)
        self.assertEqual(list(ExperimentSession.objects.filter(pk__in=[r['id'] for r in old_sessions]).order_by('id').values()), old_sessions)
        self.assertEqual(list(Attempt.objects.filter(pk__in=[r['id'] for r in old_attempts]).order_by('id').values()), old_attempts)
        new_task = baseline['tasks'][0]['task_id']
        self.assertNotEqual(new_task, self.source['tasks'][0]['task_id'])
        self.assertEqual(set(ExperimentSession.objects.filter(class_task_id=new_task).values_list('status', flat=True)), {'prepared'})
        new_experiment = Experiment.objects.get(pk=baseline['tasks'][0]['experiment_id'])
        self.assertEqual(new_experiment.class_tasks.count(), 1)
        self.assertEqual(new_experiment.versions.count(), 1)
        self.assertEqual(new_experiment.class_randomization_identities.count(), 1)
        old_snapshots = {row.pk: row for row in QuestionSnapshot.objects.filter(
            pk__in=[q['snapshot_id'] for q in self.source['accounts'][0]['questions']])}
        for old, new in zip(self.source['accounts'], baseline['accounts'], strict=True):
            self.assertEqual((old['user_id'], old['username'], old['password']), (new['user_id'], new['username'], new['password']))
            self.assertEqual(len(new['questions']), 20)
            for old_q, new_q in zip(old['questions'], new['questions'], strict=True):
                old_snapshot = old_snapshots[old_q['snapshot_id']]
                self.assertEqual(new_q['source_question_id'], old_snapshot.source_question_id)
                self.assertEqual(new_q['sequence'], old_q['sequence'])
                self.assertIn(new_q['confidence_condition'], ('prompt', 'control'))
                self.assertEqual(new_q['confidence_required'], new_q['confidence_condition'] == 'prompt')
                self.assertEqual(new_q['confidence_scale']['minimum'], 0)
                self.assertEqual(new_q['confidence_sampling_probability'], '0.500')
                self.assertIn('sha256', new_q['content_descriptor'])
        with self.assertRaisesRegex(CommandError, 'namespace|exists'):
            self.prepare()
        with self.assertRaisesRegex(CommandError, 'namespace'):
            with patch.dict(os.environ, SCHOOL_RUNTIME_ISOLATION_ENABLED='1'):
                call_command('prepare_student_capacity_baseline', source_fixture=str(self.source_path),
                             baseline_id='baseline-test', run_id='other-run',
                             output=str(Path(self.temp.name) / 'duplicate.json'), stdout=io.StringIO())

    def test_changed_account_and_all_twenty_content_bindings_are_rejected_before_creation(self):
        from apps.experiments.models import Experiment
        from .management.commands.seed_prepared_startup_load import _sha
        for mutation in ('hash', 'last-question', 'user'):
            fixture = copy.deepcopy(self.source)
            if mutation == 'hash':
                fixture['plan_sha'] = '0' * 64
            else:
                if mutation == 'last-question':
                    fixture['accounts'][-1]['questions'][-1]['content_sha256'] = '0' * 64
                else:
                    fixture['accounts'][-1]['username'] = 'unowned'
                fixture['fixture_spec']['content_binding_sha256'] = _sha([
                    {'account_id': a['account_id'], 'manifest_sha256': a['manifest_sha256'], 'questions': a['questions']}
                    for a in fixture['accounts']])
                fixture['plan_sha'] = _sha(fixture['fixture_spec'])
            self.source_path.write_text(json.dumps(fixture), encoding='utf-8')
            before = Experiment.objects.count()
            with self.subTest(mutation=mutation), self.assertRaises(CommandError):
                self.prepare()
            self.assertEqual(Experiment.objects.count(), before)
            self.assertFalse(self.output.exists())

    def test_current_source_session_state_need_not_match_archived_initial_state(self):
        from django.utils import timezone
        from .models import ExperimentSession
        first = self.source['accounts'][0]['session_id']
        ExperimentSession.objects.filter(pk=first).update(
            status='completed', started_at=timezone.now(), completed_at=timezone.now(), terminal_at=timezone.now())
        baseline = self.prepare()
        self.assertEqual(baseline['accounts'][0]['initial_state'], 'prepared')
        self.assertEqual(ExperimentSession.objects.get(pk=first).status, 'completed')

    def test_changed_source_question_scoring_is_rejected_without_rewriting_snapshot(self):
        from apps.question_bank.models import Question
        from apps.experiments.models import QuestionSnapshot
        snapshot = QuestionSnapshot.objects.get(pk=self.source['accounts'][0]['questions'][-1]['snapshot_id'])
        Question.objects.filter(pk=snapshot.source_question_id).update(correct_answer='changed')
        with self.assertRaisesRegex(CommandError, 'snapshot|content|scoring'):
            self.prepare()
