from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor
import os
from unittest import skipUnless
import uuid

from django.test import SimpleTestCase, override_settings

from .admission import (
    AdmissionCoordinator,
    AdmissionInvalid,
    AdmissionUnavailable,
)


class AdmissionCoordinatorTests(SimpleTestCase):
    def setUp(self):
        self.redis = MagicMock()
        self.redis.pipeline.return_value = self.redis
        self.coordinator = AdmissionCoordinator(self.redis)

    @override_settings(
        ASSESSMENT_ADMISSION_ENABLED=True,
        ASSESSMENT_ADMISSION_TASK_IDS=frozenset({'19'}),
        ASSESSMENT_ADMISSION_CAPACITY=8,
    )
    def test_enabled_scope_is_task_allowlisted(self):
        self.assertTrue(AdmissionCoordinator.enabled_for(19))
        self.assertFalse(AdmissionCoordinator.enabled_for(20))

    def test_request_is_idempotent_for_same_user_and_action(self):
        self.redis.eval.return_value = 'existing-token'
        self.redis.hget.return_value = 'queued'
        with patch.object(self.coordinator, '_promote'), patch(
            'apps.assessment.admission.secrets.token_urlsafe', return_value='existing-token'
        ):
            first = self.coordinator.request(task_id=19, user_id=7, action='start')
            second = self.coordinator.request(task_id=19, user_id=7, action='start')
        self.assertEqual(first.token, 'existing-token')
        self.assertEqual(first.status, 'queued')
        self.assertEqual(second.token, 'existing-token')
        self.assertEqual(self.redis.eval.call_count, 2)
        self.redis.pipeline.assert_not_called()

    def test_status_rejects_a_token_bound_to_another_user_or_action(self):
        self.redis.hmget.return_value = ['8', '19', 'resume', 'admitted']
        with self.assertRaises(AdmissionInvalid):
            self.coordinator.status(task_id=19, user_id=7, action='start', token='token')

    def test_redis_errors_are_explicit_and_do_not_fall_through(self):
        self.redis.eval.side_effect = TimeoutError('redis unavailable')
        with self.assertRaises(AdmissionUnavailable):
            self.coordinator.request(task_id=19, user_id=7, action='start')

    def test_consume_requires_an_admitted_token(self):
        self.redis.eval.return_value = 0
        with self.assertRaises(AdmissionInvalid):
            self.coordinator.consume(task_id=19, user_id=7, action='start', token='token')

    def test_terminal_request_binding_is_replaced_atomically(self):
        self.redis.get.return_value = 'expired-token'
        self.redis.hget.side_effect = lambda key, field: 'expired' if key.endswith('expired-token') else 'admitted'
        self.redis.eval.return_value = 'fresh-token'
        with patch.object(self.coordinator, '_promote'):
            try:
                result = self.coordinator.request(task_id=19, user_id=7, action='start')
            except AdmissionUnavailable:
                self.fail('A terminal request binding must permit a fresh admission request')
        self.assertEqual(result.token, 'fresh-token')
        self.assertEqual(result.status, 'admitted')
        self.redis.eval.assert_called_once()
        self.redis.set.assert_not_called()
        self.redis.pipeline.assert_not_called()


@skipUnless(os.environ.get('ADMISSION_TEST_REDIS_URL'), 'requires isolated admission Redis')
@override_settings(ASSESSMENT_ADMISSION_CAPACITY=1, ASSESSMENT_ADMISSION_LEASE_SECONDS=3,
                   ASSESSMENT_ADMISSION_TTL_SECONDS=900)
class AdmissionRedisTests(SimpleTestCase):
    def setUp(self):
        import redis
        self.client = redis.Redis.from_url(os.environ['ADMISSION_TEST_REDIS_URL'], decode_responses=True)
        self.coordinator = AdmissionCoordinator(self.client)
        self.task_id = f'test-{uuid.uuid4()}'
        self.now = 1000000.0
        self.clock = patch('apps.assessment.admission.time.time', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.cleanup_keys)

    def cleanup_keys(self):
        keys = list(self.client.scan_iter(match=f'assessment:admission:{self.task_id}:*'))
        if keys:
            self.client.delete(*keys)

    def request(self, user_id):
        return self.coordinator.request(task_id=self.task_id, user_id=user_id, action='start')

    def consume(self, user_id, token):
        return self.coordinator.consume(task_id=self.task_id, user_id=user_id, action='start', token=token)

    def test_expired_permit_can_be_requested_again_before_request_key_ttl(self):
        first = self.request(7)
        self.now += 4
        replacement = self.request(7)
        self.assertNotEqual(replacement.token, first.token)
        self.assertEqual(replacement.status, 'admitted')

    def test_consumed_permit_can_be_requested_again_before_request_key_ttl(self):
        first = self.request(7)
        self.consume(7, first.token)
        replacement = self.request(7)
        self.assertNotEqual(replacement.token, first.token)
        self.assertEqual(replacement.status, 'admitted')

    def test_missing_token_hash_does_not_strand_request_key(self):
        first = self.request(7)
        _, _, prefix = self.coordinator._keys(self.task_id)
        self.client.delete(f'{prefix}{first.token}')
        replacement = self.request(7)
        self.assertNotEqual(replacement.token, first.token)
        self.assertEqual(replacement.status, 'admitted')

    def test_concurrent_same_user_initialization_has_one_complete_live_token(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.request(7), range(24)))
        self.assertEqual(len({result.token for result in results}), 1)
        self.assertTrue(all(result.status == 'admitted' for result in results))
        queue, active, prefix = self.coordinator._keys(self.task_id)
        self.assertEqual(self.client.zcard(queue), 0)
        self.assertEqual(self.client.zcard(active), 1)
        self.assertEqual(self.client.hmget(f'{prefix}{results[0].token}', ['user_id', 'task_id', 'action']),
                         ['7', self.task_id, 'start'])

    def test_fifo_capacity_and_terminal_tokens_do_not_hold_slots(self):
        first, second, third = self.request(7), self.request(8), self.request(9)
        self.assertEqual([first.status, second.status, third.status], ['admitted', 'queued', 'queued'])
        self.consume(7, first.token)
        self.assertEqual(self.coordinator.status(task_id=self.task_id, user_id=8, action='start', token=second.token).status,
                         'admitted')
        self.assertEqual(self.coordinator.status(task_id=self.task_id, user_id=9, action='start', token=third.token).status,
                         'queued')
        self.now += 4
        self.assertEqual(self.coordinator.status(task_id=self.task_id, user_id=9, action='start', token=third.token).status,
                         'admitted')
        _, active, _ = self.coordinator._keys(self.task_id)
        self.assertEqual(self.client.zcard(active), 1)
