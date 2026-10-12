from apps.api.test_support import domain_payload
"""Run only with an explicitly isolated protocol test database."""
import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User


@override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=('*',), STUDENT_LEARNING_ANALYTICS_ENABLED=True,
                   STUDENT_SHARED_RESULTS_ENABLED=True, STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({'*'}))
class SchoolCaptureProtocolPostgresTests(TransactionTestCase):
    def test_three_fresh_students_twenty_items_use_real_protocol_and_read_only_verifier(self):
        self.exercise_workload(False)

    @override_settings(SAVE_AND_LEAVE_ENABLED=True, SAVE_AND_LEAVE_COURSE_IDS=frozenset({'*'}),
                       ATOMIC_SUBMISSION_ENABLED=True, ATOMIC_SUBMISSION_COURSE_IDS=frozenset({'*'}))
    def test_session_controller_workload_persists_three_complete_twenty_item_sessions(self):
        self.exercise_workload(True)

    def exercise_workload(self, browser_aligned):
        from apps.experiments.management.commands.verify_school_protocol_load import Command
        if connection.vendor != 'postgresql' or 'protocol' not in connection.settings_dict['NAME']:
            self.skipTest('Dedicated PostgreSQL protocol database required')
        output = StringIO()
        with patch.dict(connection.settings_dict, NAME='kt_e2e'):
            call_command('seed_assessment_load', run_id='capture-protocol-test', students=3, items=20,
                password='Local!fixture', capture_protocol=True, feedback_mode='full', stdout=output)
        fixture = json.loads(output.getvalue())
        fixture.update(database='kt_e2e', course_id=User.objects.get(username=fixture['usernames'][0]).course_enrollments.first().course_id)
        from apps.experiments.models import ClassExperimentTask
        fixture['experiment_id'] = ClassExperimentTask.objects.get(pk=fixture['task_id']).experiment_id
        sys.path.insert(0, str(Path(settings.BASE_DIR) / 'scripts/load'))
        import school_short_storm as load
        runner = load.ProtocolShortRunner('http://localhost', fixture['task_id'], 35)
        if browser_aligned:
            from capture_protocol import BROWSER_ALIGNED_WORKLOAD
            runner.workload_version = BROWSER_ALIGNED_WORKLOAD
            runner.atomic_submission = 'auto'
            runner.browser_shell_url = '/static/spa-assets/capture-shell-local-test.html'
        runner.protocol_course_id, runner.protocol_experiment_id = fixture['course_id'], fixture['experiment_id']
        clients = {}
        for name in fixture['usernames']:
            client = APIClient()
            client.force_login(User.objects.get(username=name))
            clients[name] = client

        def request(state, path, *, method='GET', body=None):
            if browser_aligned and path == runner.browser_shell_url:
                return 200, b'<html></html>', .001
            client = clients[state.username]
            response = client.generic(method, path, data=json.dumps(body).encode() if body is not None else b'',
                content_type='application/json', HTTP_ACCEPT='application/json')
            while response.status_code in (301, 302, 303, 307, 308):
                response = client.get(response.headers['Location'], HTTP_ACCEPT='application/json')
            if response.status_code >= 400:
                raise RuntimeError(f'HTTP {response.status_code}: {domain_payload(response)}')
            if browser_aligned and method == 'POST' and '/questions/' in path:
                process = domain_payload(response)['process']
                self.assertEqual(process['save_and_leave']['version'], 1)
                self.assertTrue(process['atomic_submission']['enabled'])
            return response.status_code, domain_payload(response) if 'json' in response.headers.get('Content-Type', '') else response.content, .001

        import time
        runner.released_at = time.perf_counter()
        with patch.object(load.Runner, 'request', side_effect=request):
            for name in fixture['usernames']:
                state = load.StudentState(name, 'Local!fixture')
                runner.start(state)
                if not browser_aligned:
                    runner.resume(state)
                runner.answer(state)
        protocol = runner.protocol_summary()
        self.assertEqual(protocol['completed_sessions'], 3)
        self.assertTrue(protocol['all_events_acknowledged'])
        if not browser_aligned:
            self.assertGreater(protocol['emissions'], protocol['unique_events'])
        ledger = {'task_id': fixture['task_id'], 'source_config': None, 'capture_protocol': fixture['capture_protocol'],
                  'incomplete': False, 'sessions': runner.ledger}
        options = {}
        if browser_aligned:
            ledger['workload_version'] = BROWSER_ALIGNED_WORKLOAD
            options['workload_version'] = BROWSER_ALIGNED_WORKLOAD
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = directory / 'fixture.json'
            manifest.write_text(json.dumps(fixture), encoding='utf-8')
            manifest.with_suffix('.ledger.json').write_text(json.dumps(ledger), encoding='utf-8')
            with patch.dict(connection.settings_dict, NAME='kt_e2e'):
                Command(stdout=StringIO()).handle(manifest=str(manifest), output=str(directory / 'verification.json'), cold_manifest=[], **options)
                verified = json.loads((directory / 'verification.json').read_text())
                self.assertTrue(verified['passed'])
                self.assertEqual(verified['answer_count'], 60)
                with self.assertRaises(FileExistsError):
                    Command(stdout=StringIO()).handle(manifest=str(manifest), output=str(directory / 'verification.json'), cold_manifest=[], **options)
                first = next(iter(ledger['sessions'].values()))
                next(iter(first['events'].values()))['payload_sha256'] = '0' * 64
                manifest.with_suffix('.ledger.json').write_text(json.dumps(ledger))
                with self.assertRaisesRegex(CommandError, 'persistence'):
                    Command(stdout=StringIO()).handle(manifest=str(manifest), output=str(directory / 'corrupted.json'), cold_manifest=[], **options)
