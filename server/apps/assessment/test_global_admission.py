from concurrent.futures import ThreadPoolExecutor
import json
import os
import uuid
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from .test_student_api import StudentApiFixtureMixin

from .admission import AdmissionCoordinator, AdmissionInvalid, AdmissionUnavailable


class GlobalAdmissionScopeTests(SimpleTestCase):
    @override_settings(ASSESSMENT_ADMISSION_REDIS_URL='redis://isolated-constructor-check.invalid/0')
    def test_separate_coordinators_reuse_one_process_redis_pool(self):
        with patch('redis.Redis.from_url') as factory:
            first = AdmissionCoordinator().client
            second = AdmissionCoordinator().client
        self.assertIs(first, second)
        factory.assert_called_once()

    @override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
    def test_global_operation_records_redis_work_in_existing_request_metrics(self):
        from config.capture_timing import pending_timing
        state = {}
        binding = pending_timing.set(state)
        self.addCleanup(pending_timing.reset, binding)
        client = MagicMock(eval=MagicMock(return_value='{"token":"permit","status":"executing","retry_after":0}'))
        AdmissionCoordinator(client).request(task_id=1, user_id=1, action='start')
        metrics = state.get('capture_metrics', {})
        self.assertEqual(metrics.get('admission_redis_call_count'), 1)
        self.assertEqual(metrics.get('admission_request_calls'), 1)
        self.assertGreaterEqual(metrics.get('admission_redis_seconds', -1), 0)

    @override_settings(ASSESSMENT_ADMISSION_ENABLED=True, ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True,
                       ASSESSMENT_ADMISSION_TASK_IDS=frozenset())
    def test_global_mode_covers_every_task(self):
        self.assertTrue(AdmissionCoordinator.enabled_for(987))

    def test_polling_checks_token_binding_without_reading_task_graph(self):
        from .api.views import StudentAdmissionStatusView
        from .admission import AdmissionResult
        request = MagicMock(data={'action': 'start', 'admission_token': 'permit'})
        request.user.pk = 7
        with patch('apps.assessment.api.views._student_task', side_effect=AssertionError('task graph read')), \
                patch('apps.assessment.api.views.AdmissionCoordinator') as coordinator:
            coordinator.return_value.status.return_value = AdmissionResult('permit', 'queued', 3)
            response = StudentAdmissionStatusView().post(request, task_id=19)
        self.assertEqual(response.status_code, 202)
        coordinator.return_value.status.assert_called_once_with(task_id=19, user_id=7, action='start', token='permit')

    @override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
    def test_redis_operation_is_rejected_inside_database_transaction(self):
        from django.db import connection
        client = MagicMock()
        with patch.object(connection, 'in_atomic_block', True), self.assertRaises(AdmissionUnavailable):
            AdmissionCoordinator(client).request(task_id=1, user_id=1, action='start')
        client.eval.assert_not_called()

    @override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
    def test_global_failure_logs_only_operation_cause_and_duration(self):
        for cause, response in ((TimeoutError, TimeoutError('redis://user:password@host/private-token')),
                                (json.JSONDecodeError, 'private-token secret-payload')):
            with self.subTest(cause=cause.__name__):
                client = MagicMock()
                if isinstance(response, Exception):
                    client.eval.side_effect = response
                else:
                    client.eval.return_value = response
                with patch('apps.assessment.admission.time.monotonic', side_effect=[10, 10.125]), \
                        self.assertLogs('apps.assessment.admission', level='WARNING') as logs, \
                        self.assertRaises(AdmissionUnavailable) as caught:
                    AdmissionCoordinator(client).request(task_id=998877, user_id=887766, action='start')
                self.assertIsInstance(caught.exception.__cause__, cause)
                if isinstance(response, Exception):
                    self.assertIs(caught.exception.__cause__, response)
                self.assertEqual(len(logs.records), 1)
                record = logs.records[0]
                self.assertEqual(record.getMessage(),
                                 f'Admission Redis failure operation=request cause={cause.__name__} duration_ms=125.000')
                self.assertIsNone(record.exc_info)
                self.assertNotIn('private-token', logs.output[0])
                self.assertNotIn('password', logs.output[0])
                self.assertNotIn('998877', logs.output[0])
                self.assertNotIn('887766', logs.output[0])

    @override_settings(ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
    def test_invalid_global_token_keeps_invalid_semantics_without_failure_log(self):
        client = MagicMock(eval=MagicMock(return_value='{"invalid": true}'))
        with self.assertNoLogs('apps.assessment.admission', level='WARNING'), self.assertRaises(AdmissionInvalid):
            AdmissionCoordinator(client).request(task_id=1, user_id=1, action='start')


@override_settings(ASSESSMENT_PREPARED_ENABLED=False, ASSESSMENT_ADMISSION_ENABLED=False,
                   ASSESSMENT_ADMISSION_GLOBAL_ENABLED=False)
class GlobalResumeGuardTests(StudentApiFixtureMixin, TestCase):
    def test_started_paper_resume_does_not_reserve_an_activation_slot(self):
        from .admission import AdmissionResult
        self.assertEqual(self.start().status_code, 201)
        self.client.force_login(self.student)
        with patch('apps.assessment.api.views.AdmissionCoordinator') as coordinator:
            coordinator.global_enabled.return_value = True
            coordinator.enabled_for.return_value = True
            coordinator.return_value.request.return_value = AdmissionResult('permit', 'admitted', 0)
            response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/resume/',
                data={}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        coordinator.return_value.request.assert_not_called()


@skipUnless(os.environ.get('ADMISSION_TEST_REDIS_URL'), 'requires isolated admission Redis')
class GlobalAdmissionRedisTests(SimpleTestCase):
    def setUp(self):
        import redis
        self.client = redis.Redis.from_url(os.environ['ADMISSION_TEST_REDIS_URL'], decode_responses=True)
        self.prefix = f'assessment:admission:test-global-{uuid.uuid4()}'
        self.settings = override_settings(
            ASSESSMENT_ADMISSION_ENABLED=True, ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True,
            ASSESSMENT_ADMISSION_KEY_PREFIX=self.prefix, ASSESSMENT_ADMISSION_CAPACITY=2,
            ASSESSMENT_ADMISSION_RATE_PER_SECOND=1000000, ASSESSMENT_ADMISSION_BATCH_SIZE=8,
            ASSESSMENT_ADMISSION_LEASE_SECONDS=3, ASSESSMENT_ADMISSION_TTL_SECONDS=900,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.addCleanup(self.cleanup_keys)
        self.coordinator = AdmissionCoordinator(self.client)

    def cleanup_keys(self):
        keys = list(self.client.scan_iter(match=f'{self.prefix}:*'))
        if keys:
            self.client.delete(*keys)

    def request(self, task=1, user=1):
        return self.coordinator.request(task_id=task, user_id=user, action='start')

    def identity(self, token, task=1, user=1):
        return dict(task_id=task, user_id=user, action='start', token=token)

    def status(self, token, task=1, user=1):
        return self.coordinator.status(**self.identity(token, task, user))

    def test_capacity_counts_executions_across_tasks_without_reservations(self):
        results = [self.request(task=n, user=n) for n in (1, 2, 3)]
        self.assertEqual([r.status for r in results], ['executing', 'executing', 'busy'])
        self.assertTrue(all(r.execution_claimed for r in results[:2]))
        self.assertEqual(results[2].token, '')
        self.assertEqual(self.coordinator.occupancy(), {'admitted': 0, 'executing': 2, 'occupied': 2})
        self.assertFalse(list(self.client.scan_iter(match=self.prefix + ':queue:*')))
        self.assertFalse(self.client.exists(self.prefix + ':tasks', self.prefix + ':leases'))
        self.assertIsNone(self.client.get(self.prefix + ':request:3:3:start'))

    def test_concurrent_requests_cannot_over_acquire(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: self.request(task=n, user=n), range(1, 25)))
        self.assertEqual(sum(r.execution_claimed for r in results), 2)
        self.assertEqual(sum(r.status == 'busy' for r in results), 22)
        self.assertEqual(self.client.zcard(self.prefix + ':executing'), 2)

    def test_repeated_request_returns_the_same_execution_without_claiming_twice(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.request(), range(24)))
        self.assertEqual(sum(r.execution_claimed for r in results), 1)
        self.assertEqual(len({r.token for r in results}), 1)
        self.assertEqual(len({r.execution_id for r in results}), 1)
        self.assertEqual(self.client.zcard(self.prefix + ':executing'), 1)
        saved = self.client.hgetall(self.prefix + ':token:' + results[0].token)
        self.assertEqual(saved['requested_at'], saved['execution_started_at'])
        self.assertNotIn('admitted_at', saved)

    def test_rate_burst_one_is_independent_of_capacity_and_client_clock(self):
        with override_settings(ASSESSMENT_ADMISSION_RATE_PER_SECOND=0.01), \
                patch('apps.assessment.admission.time.time', return_value=1e20):
            first = self.request()
            self.assertEqual(first.status, 'executing')
            self.coordinator.release(**self.identity(first.token), execution_id=first.execution_id, success=True)
            self.assertEqual(self.request(task=2, user=2).status, 'busy')
            self.assertEqual(self.coordinator.occupancy()['occupied'], 0)
        seconds, micros = self.client.time()
        self.client.hset(self.prefix + ':bucket', 'at', seconds + micros / 1e6 - 2)
        self.assertTrue(self.request(task=2, user=2).execution_claimed)

    def test_status_is_read_only_and_checks_identity(self):
        first = self.request()
        bucket = self.client.hgetall(self.prefix + ':bucket')
        saved = self.client.hgetall(self.prefix + ':token:' + first.token)
        self.assertEqual(self.status(first.token).status, 'executing')
        self.assertEqual(self.client.hgetall(self.prefix + ':bucket'), bucket)
        self.assertEqual(self.client.hgetall(self.prefix + ':token:' + first.token), saved)
        with self.assertRaises(AdmissionInvalid):
            self.status(first.token, user=99)

    def test_expired_execution_remains_occupied_until_proven_recovery(self):
        with override_settings(ASSESSMENT_ADMISSION_CAPACITY=1):
            first = self.request()
            self.client.hset(self.prefix + ':token:' + first.token, 'lease_until', 0)
            self.assertEqual(self.client.ttl(self.prefix + ':token:' + first.token), -1)
            self.assertEqual(self.request(task=2, user=2).status, 'busy')
            self.assertEqual(self.status(first.token).status, 'executing')
            self.assertTrue(self.coordinator.executing_page(limit=1)[1][0].recovery_due)
            with self.assertRaises(AdmissionInvalid):
                self.coordinator.release(**self.identity(first.token), execution_id='wrong', success=True)
            self.coordinator.recover(**self.identity(first.token), execution_id=first.execution_id, success=False)
            self.assertTrue(self.request(task=2, user=2).execution_claimed)

    def test_execution_listing_is_bounded(self):
        self.request()
        self.request(task=2, user=2)
        cursor, rows = self.coordinator.executing_page(limit=1)
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(cursor, 0)
        cursor, rows = self.coordinator.executing_page(cursor=cursor, limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(cursor, 0)

    def test_successful_release_and_replay_are_idempotent(self):
        first = self.request()
        identity = dict(**self.identity(first.token), execution_id=first.execution_id, success=True)
        self.assertEqual(self.coordinator.release(**identity).status, 'succeeded')
        saved = self.client.hgetall(self.prefix + ':token:' + first.token)
        self.coordinator.release(**identity)
        repeated = self.request()
        self.assertEqual(repeated.status, 'succeeded')
        self.assertFalse(repeated.execution_claimed)
        self.assertEqual(repeated.token, first.token)
        self.assertEqual(self.client.hgetall(self.prefix + ':token:' + first.token), saved)
        self.assertEqual(self.coordinator.occupancy()['occupied'], 0)
        with self.assertRaises(AdmissionInvalid):
            self.coordinator.release(**{**identity, 'success': False})

    def test_failed_execution_allows_fresh_request(self):
        first = self.request()
        self.coordinator.release(**self.identity(first.token), execution_id=first.execution_id, success=False)
        fresh = self.request()
        self.assertTrue(fresh.execution_claimed)
        self.assertNotEqual(fresh.token, first.token)

    def test_lost_acquisition_response_replays_only_the_original_server_execution(self):
        import redis
        real_eval = self.client.eval
        calls, first_state = [], {}
        def lose_first_response(*args):
            raw = real_eval(*args)
            calls.append(args)
            if len(calls) == 1:
                first_state.update(self.client.hgetall(self.prefix + ':token:' + json.loads(raw)['token']))
                raise redis.exceptions.TimeoutError('response lost after Redis executed')
            return raw
        wrapped = MagicMock(wraps=self.client)
        wrapped.eval.side_effect = lose_first_response
        from config.capture_timing import pending_timing
        state = {}
        binding = pending_timing.set(state)
        try:
            result = AdmissionCoordinator(wrapped).request(task_id=1, user_id=1, action='start')
        finally:
            pending_timing.reset(binding)
        self.assertEqual(state['capture_metrics']['admission_redis_call_count'], 2)
        self.assertEqual(state['capture_metrics']['admission_request_calls'], 2)
        self.assertTrue(result.execution_claimed)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(self.client.hgetall(self.prefix + ':token:' + result.token), first_state)
        self.assertEqual(self.coordinator.occupancy()['occupied'], 1)
        another_http_request = self.request()
        self.assertEqual(another_http_request.execution_id, result.execution_id)
        self.assertFalse(another_http_request.execution_claimed)

    def test_repeated_lost_acquisition_responses_stay_bounded_and_conservative(self):
        import redis
        real_eval = self.client.eval
        wrapped = MagicMock(wraps=self.client)
        def lose_every_response(*args):
            real_eval(*args)
            raise redis.exceptions.TimeoutError('response lost after Redis executed')
        wrapped.eval.side_effect = lose_every_response
        with self.assertRaises(AdmissionUnavailable):
            AdmissionCoordinator(wrapped).request(task_id=1, user_id=1, action='start')
        self.assertEqual(wrapped.eval.call_count, 2)
        self.assertEqual(self.coordinator.occupancy()['occupied'], 1)
        self.assertFalse(self.request().execution_claimed)

    def test_redis_error_fails_closed(self):
        coordinator = AdmissionCoordinator(MagicMock(eval=MagicMock(side_effect=TimeoutError)))
        with self.assertRaises(AdmissionUnavailable):
            coordinator.request(task_id=1, user_id=1, action='start')
