import importlib.util
import json
import os
from pathlib import Path
import tempfile
import uuid
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings


class PreparedAdmissionObserverTests(SimpleTestCase):
    def module(self):
        path = settings.BASE_DIR / 'scripts/load/observe_prepared_admission.py'
        spec = importlib.util.spec_from_file_location('prepared_admission_observer', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self):
        return {'isolated_demo': True, 'settings_binding': {'ASSESSMENT_ADMISSION_CAPACITY': 2},
                'accounts': [{'account_id': f'account-{n}', 'user_id': n + 1, 'taskId': 9,
                              'session_id': n + 100, 'username': 'private', 'password': 'secret'}
                             for n in range(180)]}

    def test_atomic_read_only_snapshot_redacts_token_and_preserves_real_fields(self):
        module = self.module()
        accounts, capacity = module.fixture_bindings(self.fixture())
        rows = [{'token': 'private-token', 'values': ['1', '9', 'start', 'executing', 'uuid', '130',
                                                        None, None, '101.25', None],
                 'executing_score': '101.25', 'lease_score': None}] + [{}] * 179
        client = Mock()
        client.eval_ro.return_value = json.dumps({'server_time': 105.5, 'admitted': 1, 'executing': 1, 'rows': rows})
        sample = module.read_sample(client, 'isolated:admission', accounts)
        client.eval_ro.assert_called_once()
        self.assertEqual(sample['occupied'], 2)
        row = sample['accounts'][0]
        self.assertEqual(row['execution_started_at'], 101.25)
        self.assertEqual(row['lease_until'], 130)
        self.assertEqual(row['missing_timestamp_fields'], ['requested_at', 'admitted_at', 'released_at'])
        self.assertEqual(len(row['token_sha256']), 64)
        self.assertNotIn('private-token', json.dumps(sample))
        self.assertNotIn('secret', json.dumps(accounts))
        self.assertEqual(capacity, 2)
        self.assertNotIn("redis.call('HSET'", module.SNAPSHOT_SCRIPT)

    def test_invalid_binding_and_oversized_fixture_fail_without_exposing_values(self):
        module = self.module()
        fixture = self.fixture()
        fixture['accounts'].append(fixture['accounts'][0])
        with self.assertRaises(ValueError):
            module.fixture_bindings(fixture)
        accounts, _ = module.fixture_bindings(self.fixture())
        client = Mock()
        client.eval_ro.return_value = json.dumps({'server_time': 1, 'admitted': 0, 'executing': 0,
            'rows': [{'token': 'private-token', 'values': ['999', '9', 'start', 'succeeded', '', '',
                                                        None, None, '101.25', None]}] + [{}] * 179})
        row = module.read_sample(client, 'isolated', accounts)['accounts'][0]
        self.assertEqual(row['measurement'], 'missing')
        self.assertNotIn('succeeded', json.dumps(row))

    def test_summary_is_observed_only_and_missing_exact_durations_stay_missing(self):
        module = self.module()
        summary = module.Summary(capacity=2, fixture_sha256='abc')
        summary.add({'measurement': 'sampled', 'server_time': 10, 'occupied': 2, 'admitted': 1,
                     'executing': 1, 'accounts': [{'user_id': 1, 'task_id': 9, 'session_id': 100,
                     'token_sha256': 'hash', 'state': 'executing', 'execution_started_at': 9}]})
        summary.add({'measurement': 'sampled', 'server_time': 11, 'occupied': 0, 'admitted': 0,
                     'executing': 0, 'accounts': [{'user_id': 1, 'task_id': 9, 'session_id': 100,
                     'token_sha256': 'hash', 'state': 'succeeded'}]})
        summary.add({'measurement': 'missing', 'error_type': 'ConnectionError'})
        summary.add({'measurement': 'sampled', 'server_time': 12, 'occupied': 0, 'admitted': 0,
                     'executing': 0, 'accounts': [{'user_id': 1, 'task_id': 9, 'session_id': 100,
                                                 'measurement': 'missing', 'reason': 'request_binding_absent'}]})
        result = summary.result()
        self.assertEqual(result['measurement'], 'partial')
        self.assertEqual(result['peak_occupied_observed'], 2)
        self.assertEqual(result['global_c_from_fixture'], 2)
        self.assertEqual(result['accounts'][0]['released_state_observed'], 'succeeded')
        self.assertIsNone(result['accounts'][0]['execution_seconds'])
        self.assertIsNone(result['accounts'][0]['reservation_seconds'])
        self.assertNotIn('verdict', result)

    def test_cli_failure_summary_does_not_leak_redis_exception_credentials(self):
        import redis
        module = self.module()
        client = Mock()
        client.eval_ro.side_effect = redis.ConnectionError('redis://private:secret@host/private-token')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'fixture.json').write_text(json.dumps(self.fixture()))
            with patch('redis.Redis.from_url', return_value=client):
                code = module.main(['--redis-url', 'redis://unused', '--prefix', 'isolated',
                    '--fixture', str(root / 'fixture.json'), '--output', str(root / 'samples.jsonl'),
                    '--summary', str(root / 'summary.json'), '--stop-file', str(root / 'stop'), '--duration', '0.01'])
            summary = json.loads((root / 'summary.json').read_text())
            evidence = (root / 'samples.jsonl').read_text()
        self.assertEqual(code, 2)
        self.assertEqual(summary['measurement'], 'missing')
        self.assertIsNone(summary['peak_occupied_observed'])
        self.assertNotIn('private', evidence)
        self.assertNotIn('secret', evidence)

    @skipUnless(os.environ.get('ADMISSION_TEST_REDIS_URL'), 'requires isolated admission Redis')
    def test_real_redis_readonly_acl_and_cli_keep_state_and_exact_lifecycle(self):
        import redis
        from apps.assessment.admission import AdmissionCoordinator
        module = self.module()
        client = redis.Redis.from_url(os.environ['ADMISSION_TEST_REDIS_URL'], decode_responses=True)
        prefix = 'assessment:observer-test:' + uuid.uuid4().hex
        username, password = 'observer-' + uuid.uuid4().hex, uuid.uuid4().hex
        try:
            with override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True, ASSESSMENT_ADMISSION_KEY_PREFIX=prefix,
                                   ASSESSMENT_ADMISSION_CAPACITY=2, ASSESSMENT_ADMISSION_RATE_PER_SECOND=1000000):
                coordinator = AdmissionCoordinator(client)
                claim = coordinator.request(task_id=9, user_id=1, action='start')
                token = claim.token
                self.assertTrue(claim.execution_claimed)
                identity = dict(task_id=9, user_id=1, action='start', token=token)
                coordinator.release(**identity, execution_id=claim.execution_id, success=True)
            client.zadd(prefix + ':leases', {'expired-reservation': 0})
            before = client.hgetall(prefix + ':token:' + token)
            client.execute_command('ACL', 'SETUSER', username, 'on', '>' + password, '~' + prefix + '*',
                                   '+eval_ro', '+time', '+get', '+hmget', '+zcard', '+select', '+ping')
            readonly = redis.Redis.from_url(os.environ['ADMISSION_TEST_REDIS_URL'],
                                           username=username, password=password, decode_responses=True)
            accounts, _ = module.fixture_bindings(self.fixture())
            sample = module.read_sample(readonly, prefix, accounts)
            readonly.close()
            self.assertEqual(sample['occupied'], 1)  # stale reservations remain conservatively counted
            self.assertEqual(sample['accounts'][0]['released_at'], float(before['released_at']))
            self.assertEqual(sample['accounts'][0]['missing_timestamp_fields'], ['admitted_at'])
            self.assertEqual(client.hgetall(prefix + ':token:' + token), before)
            self.assertEqual(client.zcard(prefix + ':leases'), 1)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'fixture.json').write_text(json.dumps(self.fixture()))
                code = module.main(['--redis-url', os.environ['ADMISSION_TEST_REDIS_URL'], '--prefix', prefix,
                    '--fixture', str(root / 'fixture.json'), '--output', str(root / 'samples.jsonl'),
                    '--summary', str(root / 'summary.json'), '--stop-file', str(root / 'stop'), '--duration', '0.15'])
                summary = json.loads((root / 'summary.json').read_text())
                evidence = (root / 'samples.jsonl').read_text()
            self.assertEqual(code, 0)
            self.assertEqual(summary['peak_occupied_observed'], 1)
            self.assertGreaterEqual(summary['accounts'][0]['execution_seconds'], 0)
            self.assertNotIn(token, evidence)
            self.assertNotIn('secret', evidence)
        finally:
            client.execute_command('ACL', 'DELUSER', username)
            keys = list(client.scan_iter(prefix + '*'))
            if keys:
                client.delete(*keys)
            client.close()
