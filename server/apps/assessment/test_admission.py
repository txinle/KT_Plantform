from unittest.mock import MagicMock, patch

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
        self.redis.get.side_effect = [None, 'existing-token']
        self.redis.set.side_effect = [True, False]
        self.redis.incr.return_value = 4
        self.redis.hget.return_value = 'queued'
        with patch.object(self.coordinator, '_promote'), patch(
            'apps.assessment.admission.secrets.token_urlsafe', return_value='existing-token'
        ):
            first = self.coordinator.request(task_id=19, user_id=7, action='start')
            second = self.coordinator.request(task_id=19, user_id=7, action='start')
        self.assertEqual(first.token, 'existing-token')
        self.assertEqual(first.status, 'queued')
        self.assertEqual(second.token, 'existing-token')
        self.redis.pipeline.assert_called_once()

    def test_status_rejects_a_token_bound_to_another_user_or_action(self):
        self.redis.hmget.return_value = ['8', '19', 'resume', 'admitted']
        with self.assertRaises(AdmissionInvalid):
            self.coordinator.status(task_id=19, user_id=7, action='start', token='token')

    def test_redis_errors_are_explicit_and_do_not_fall_through(self):
        self.redis.get.side_effect = TimeoutError('redis unavailable')
        with self.assertRaises(AdmissionUnavailable):
            self.coordinator.request(task_id=19, user_id=7, action='start')

    def test_consume_requires_an_admitted_token(self):
        self.redis.eval.return_value = 0
        with self.assertRaises(AdmissionInvalid):
            self.coordinator.consume(task_id=19, user_id=7, action='start', token='token')
