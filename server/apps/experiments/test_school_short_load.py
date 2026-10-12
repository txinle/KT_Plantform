from apps.api.test_support import domain_payload
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch
import json
import urllib.error

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase


class SchoolShortLoadTests(SimpleTestCase):
    def module(self):
        sys.path.insert(0, str(Path(settings.BASE_DIR) / 'scripts/load'))
        import school_short_storm
        return school_short_storm

    def test_short_fixture_rejects_other_database_before_reading_manifest(self):
        with self.assertRaisesRegex(CommandError, 'kt_e2e'):
            call_command('seed_school_short_load', manifest='not-read', output_dir='not-created', source_config='not-read', stdout=StringIO())

    def test_login_is_bounded_but_start_resume_and_answer_are_synchronized(self):
        module = self.module()
        runner = Mock(errors=[])
        states = list(range(150))
        module.run_phases(runner, states)
        calls = runner.phase.call_args_list
        self.assertEqual([call.args[0] for call in calls], ['login', 'start', 'resume', 'answer'])
        self.assertEqual(calls[0].args[3], 6)
        self.assertFalse(calls[0].kwargs['synchronized'])
        for call in calls[1:]:
            self.assertEqual(call.args[3], 150)
            self.assertTrue(call.kwargs['synchronized'])

    def test_retry_preserves_body_identity_and_counts_transport_error(self):
        module = self.module()
        runner = module.ShortRunner('http://localhost', 3, 35)
        state = module.StudentState('private-user', 'private-password')
        body = {'submission_token': 'same-idempotency-key'}
        with patch.object(module.Runner, 'request', side_effect=[RuntimeError('transport'), (200, {}, .1)]) as request:
            runner.request(state, '/submit/', method='POST', body=body)
        self.assertEqual(request.call_count, 2)
        self.assertIs(request.call_args_list[0].kwargs['body'], request.call_args_list[1].kwargs['body'])

    def test_conflict_is_not_blindly_retried(self):
        module = self.module()
        runner = module.ShortRunner('http://localhost', 3, 35)
        state = module.StudentState('fixture', 'fixture')
        with patch.object(module.Runner, 'request', side_effect=RuntimeError('HTTP 409')) as request:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 409'):
                runner.request(state, '/api/v1/student/attempts/1/draft/', method='POST', body={'expected_version': 0})
        self.assertEqual(request.call_count, 1)

    def test_each_attempt_retains_status_or_transport_type_without_credentials(self):
        module = self.module()
        runner = module.ShortRunner('http://localhost', 3, 35)
        state = module.StudentState('private-user', 'private-password')
        response = Mock(status=200, headers={'Content-Type': 'application/json', 'X-Capture-Seconds': '0.125000'})
        response.read.return_value = b'{}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        state.opener = Mock()
        state.opener.open.side_effect = [
            urllib.error.HTTPError('http://localhost/private', 503, 'SECRET', {}, None), response,
        ]
        status, _, _ = runner.request(state, '/assessment/attempts/123/save/', method='POST', body={'answer': 'PRIVATE ANSWER'})
        self.assertEqual(status, 200)
        self.assertEqual(len(runner.request_attempts), 2)
        first, second = runner.request_attempts
        self.assertEqual(first['http_status'], 503)
        self.assertEqual(first['error_type'], 'HTTPError')
        self.assertEqual(second['http_status'], 200)
        self.assertEqual(second['application_seconds'], .125)
        self.assertEqual([first['attempt'], second['attempt']], [1, 2])
        self.assertEqual(first['operation_id'], second['operation_id'])
        self.assertNotEqual(first['request_id'], second['request_id'])
        self.assertGreaterEqual(first['elapsed_seconds'], 0)
        self.assertEqual(runner.request_count, 2)
        self.assertEqual(runner.request_errors, 1)
        summary = runner.attempt_summary()
        self.assertEqual(summary['first_attempt_success_rate'], 0)
        self.assertEqual(summary['retry_rate'], 1)
        self.assertEqual(summary['recovered_operations'], 1)
        for private in ('private-user', 'private-password', 'PRIVATE ANSWER', 'SECRET', '/123/'):
            self.assertNotIn(private, json.dumps(runner.request_attempts))

    def test_transport_error_preserves_exception_class_and_operation_failure(self):
        module = self.module()
        runner = module.ShortRunner('http://localhost', 3, 35)
        state = module.StudentState('private-user', 'private-password')
        state.opener = Mock()
        state.opener.open.side_effect = TimeoutError('PRIVATE MESSAGE')
        with self.assertRaises(RuntimeError):
            runner.request(state, '/assessment/attempts/123/save/', method='POST', body={'answer': 'SECRET'})
        self.assertEqual(len(runner.request_attempts), 2)
        self.assertEqual([row['error_type'] for row in runner.request_attempts], ['TimeoutError', 'TimeoutError'])
        self.assertTrue(all(row['http_status'] is None for row in runner.request_attempts))
        self.assertEqual(runner.attempt_summary()['failed_operations'], 1)
        self.assertNotIn('SECRET', json.dumps(runner.request_attempts))
        self.assertNotIn('PRIVATE MESSAGE', json.dumps(runner.request_attempts))

    def test_late_item_records_scheduled_arrival_without_shifting_schedule(self):
        module = self.module()
        runner = module.ShortRunner('http://localhost', 3, 35, item_interval=18)
        state = module.StudentState('private-user', 'private-password')
        with patch.object(module.time, 'monotonic', side_effect=[100, 130, 160]):
            runner.before_item(state, 0)
            runner.before_item(state, 1)
            runner.before_item(state, 2)
        self.assertEqual([row['planned_offset_seconds'] for row in runner.item_arrivals], [0, 18, 36])
        self.assertEqual([row['send_delay_seconds'] for row in runner.item_arrivals], [0, 12, 24])

    def test_gate_rejects_slow_question_entry_and_write_tails(self):
        module = self.module()
        metrics = {key: {'count': 50 * (20 if key in ('enter_item', 'save_draft', 'event_batch', 'submit_item') else 1),
                        'p95_seconds': .2, 'p99_seconds': .3}
                   for key in ('login', 'start', 'resume', 'enter_item', 'save_draft', 'event_batch', 'submit_item', 'finish')}
        result = {'student_count': 50, 'metrics': metrics, 'errors': [], 'http_error_count': 0,
                  'session_count': 50, 'unique_session_count': 50}
        self.assertTrue(all(module.checks(result).values()))
        for key, tail_limit in (('enter_item', 2), ('save_draft', 2), ('event_batch', 2), ('submit_item', 5), ('finish', 5)):
            with self.subTest(key=key):
                changed = {**result, 'metrics': {**metrics, key: {**metrics[key], 'p99_seconds': tail_limit + .001}}}
                self.assertFalse(all(module.checks(changed).values()))
        changed = {**result, 'metrics': {**metrics, 'enter_item': {**metrics['enter_item'], 'p95_seconds': 1.001}}}
        self.assertFalse(all(module.checks(changed).values()))

    def test_gate_requires_all_students_and_every_write_sample_and_p99(self):
        module = self.module()
        metrics = {key: {'count': 50 * (20 if key in ('enter_item', 'save_draft', 'event_batch', 'submit_item') else 1),
                         'p95_seconds': .2, 'p99_seconds': .3}
                   for key in ('login', 'start', 'resume', 'enter_item', 'save_draft', 'event_batch', 'submit_item', 'finish')}
        result = {'student_count': 50, 'metrics': metrics, 'errors': [], 'http_error_count': 0,
                  'session_count': 50, 'unique_session_count': 50}
        self.assertTrue(all(module.checks(result).values()))
        self.assertFalse(module.checks({**result, 'metrics': {**metrics, 'start': {**metrics['start'], 'p99_seconds': 10.01}}})['start_p99'])
        self.assertFalse(module.checks({**result, 'metrics': {**metrics, 'submit_item': {**metrics['submit_item'], 'count': 999}}})['complete_workflow'])
        self.assertFalse(module.checks({**result, 'http_error_count': 1})['zero_http_errors'])

    def test_three_hundred_short_has_representative_intervals(self):
        module = self.module()
        self.assertEqual(module.item_interval(3), 0)
        self.assertEqual(module.item_interval(50), 18)
        self.assertEqual(module.item_interval(300), 18)

    def test_three_student_smoke_is_default_first_and_still_requires_sixty_writes(self):
        from apps.experiments.management.commands.seed_school_short_load import Command
        import inspect
        self.assertEqual(inspect.signature(Command._seed).parameters['sizes'].default, (3, 50, 150, 300))
        module = self.module()
        metrics = {key: {'count': 3 * (20 if key in ('enter_item', 'save_draft', 'event_batch', 'submit_item') else 1),
                         'p95_seconds': .1, 'p99_seconds': .2}
                   for key in ('login', 'start', 'resume', 'enter_item', 'save_draft', 'event_batch', 'submit_item', 'finish')}
        value = {'student_count': 3, 'metrics': metrics, 'errors': [], 'http_error_count': 0,
                 'session_count': 3, 'unique_session_count': 3}
        self.assertTrue(all(module.checks(value).values()))
        metrics['event_batch']['count'] = 59
        self.assertFalse(module.checks(value)['complete_workflow'])

    def test_http200_duplicate_ack_is_rejected_and_generated_event_retained(self):
        module = self.module()
        runner = module.ShortRunner('http://localhost', 1, 35)
        state = module.StudentState('private-user', 'private-password')
        state.session_id = 12
        body = {'client_instance_id': 'client', 'events': [{'event_id': 'event', 'client_sequence_no': 1, 'payload': {'value': True}}]}
        ack = {'event_id': 'event', 'client_sequence_no': 1}
        with patch.object(module.Runner, 'request', return_value=(200, {'success': True, 'rejections': [], 'acknowledgements': [ack, ack]}, .1)):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                runner.request(state, '/events/', method='POST', body=body)
        self.assertEqual(runner.ledger['12']['event_ids'], ['event'])
        self.assertFalse(runner.ledger['12']['events']['event']['acknowledged'])


class SchoolShortFixturePostgresTests(TestCase):
    def test_real_publication_reuses_users_preserves_formal_membership_and_freezes_new_task(self):
        import json
        import tempfile
        from django.db import connection
        from apps.accounts.models import User, CourseEnrollment
        from apps.experiments.models import ClassExperimentTask, ExperimentVersionParticipantSnapshot
        from apps.experiments.management.commands.seed_school_short_load import Command
        from django.contrib.auth.hashers import make_password
        encoded = make_password('Normal!fixture')

        def create_existing(**kwargs):
            kwargs['password'] = encoded
            return User.objects.create(**kwargs)

        output = StringIO()
        with patch.object(User.objects, 'create_user', side_effect=create_existing), patch.dict(connection.settings_dict, NAME='kt_e2e'):
            call_command('seed_assessment_load', run_id='short-contract', students=300, items=20, password='Normal!fixture', stdout=output)
        fixture = json.loads(output.getvalue())
        fixture['database'] = 'kt_e2e'
        baseline = ClassExperimentTask.objects.get(pk=fixture['task_id'])
        memberships = list(CourseEnrollment.objects.values_list('pk', 'course_id', 'class_group_id', 'student_id'))
        users = dict(User.objects.values_list('pk', 'password'))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'baseline.json').write_text(json.dumps(fixture))
            (directory / 'source.json').write_text(json.dumps({'source_sha256': 'a' * 64}))
            command = Command(stdout=StringIO())
            command._seed({'manifest': str(directory / 'baseline.json'), 'source_config': str(directory / 'source.json'),
                           'output_dir': str(directory / 'shorts')}, sizes=(3, 50))
            smoke = json.loads((directory / 'shorts/short-3.json').read_text())
            self.assertEqual(ExperimentVersionParticipantSnapshot.objects.filter(class_task_id=smoke['task_id']).count(), 3)
            self.verify_real_capture_then_reject_incomplete_class(directory, smoke)
            short = json.loads((directory / 'shorts/short-50.json').read_text())
            self.verify_real_capture_then_reject_incomplete_class(directory, short)
        self.assertEqual(dict(User.objects.values_list('pk', 'password')), users)
        self.assertEqual(list(CourseEnrollment.objects.filter(pk__in=[row[0] for row in memberships]).values_list('pk', 'course_id', 'class_group_id', 'student_id')), memberships)
        self.assertNotEqual(ClassExperimentTask.objects.get(pk=short['task_id']).experiment.course_id, baseline.experiment.course_id)
        self.assertEqual(ExperimentVersionParticipantSnapshot.objects.filter(class_task_id=short['task_id']).count(), 50)
        self.assertFalse(baseline.sessions.exists())

    def verify_real_capture_then_reject_incomplete_class(self, directory, short):
        import json
        import time
        from django.db import connection
        from rest_framework.test import APIClient
        from apps.accounts.models import User
        from apps.assessment.models import AssessmentEvent, Attempt
        from apps.experiments.management.commands.verify_school_short_load import Command
        sys.path.insert(0, str(Path(settings.BASE_DIR) / 'scripts/load'))
        import school_short_storm as load
        client = APIClient()
        client.force_login(User.objects.get(username=short['usernames'][0]))
        state = load.StudentState(short['usernames'][0], 'Normal!fixture')
        runner = load.ShortRunner('http://localhost', short['task_id'], 35)
        runner.released_at = time.perf_counter()

        def real_request(_, path, *, method='GET', body=None):
            response = client.generic(method, path, data=json.dumps(body).encode() if body is not None else b'', content_type='application/json', HTTP_ACCEPT='application/json')
            while response.status_code in (301, 302, 303, 307, 308):
                response = client.get(response.headers['Location'], HTTP_ACCEPT='application/json')
            if response.status_code >= 400:
                raise RuntimeError(f'HTTP {response.status_code}')
            return response.status_code, domain_payload(response) if 'json' in response.headers.get('Content-Type', '') else response.content, .001

        with patch.object(load.Runner, 'request', side_effect=real_request):
            runner.start(state)
            runner.resume(state)
            runner.answer(state)
        row = runner.ledger[str(state.session_id)]
        self.assertEqual(len(row['event_ids']), 20)
        self.assertEqual(AssessmentEvent.objects.filter(event_id__in=row['event_ids']).count(), 20)
        for event in AssessmentEvent.objects.filter(event_id__in=row['event_ids']):
            self.assertEqual(load.payload_hash(event.payload), row['events'][str(event.event_id)]['payload_sha256'])
            self.assertEqual(event.client_sequence_no, row['events'][str(event.event_id)]['client_sequence_no'])
            self.assertTrue(row['events'][str(event.event_id)]['acknowledged'])
        self.assertEqual(Attempt.objects.filter(session_id=state.session_id, submission_token__in=row['submission_tokens']).count(), 20)
        path = directory / 'shorts' / f"short-{short['student_count']}.json"
        path.with_suffix('.ledger.json').write_text(json.dumps({'task_id': short['task_id'], 'source_config': short['source_config'], 'sessions': runner.ledger}))
        report = directory / f"persistence-{short['student_count']}.json"
        with patch.dict(connection.settings_dict, NAME='kt_e2e'), self.assertRaisesRegex(CommandError, 'persistence'):
            Command(stdout=StringIO()).handle(manifest=str(path), output=str(report), cold_manifest=[])
        original = report.read_bytes()
        self.assertFalse(json.loads(original)['passed'])
        with patch.dict(connection.settings_dict, NAME='kt_e2e'), self.assertRaises(FileExistsError):
            Command(stdout=StringIO()).handle(manifest=str(path), output=str(report), cold_manifest=[])
        self.assertEqual(report.read_bytes(), original)
