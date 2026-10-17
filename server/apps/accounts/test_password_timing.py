"""Time the native password checker without changing its hash or semantics."""
import json
from unittest.mock import patch

from django.contrib.auth.hashers import get_hasher, identify_hasher, make_password
from django.contrib.auth.models import AbstractUser
from django.test import SimpleTestCase

from config.capture_timing import pending_timing
from .models import User


class PasswordCheckTimingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw_password = 'private-real-strong-password'
        cls.encoded = make_password(cls.raw_password)

    def setUp(self):
        self.user = User(username='private-account-name', password=self.encoded)

    def timing_state(self, state):
        token = pending_timing.set(state)
        self.addCleanup(pending_timing.reset, token)

    def test_real_correct_and_incorrect_password_checks_accumulate_without_secrets(self):
        state = {'capture_metrics': {'existing_seconds': 3.0}}
        self.timing_state(state)
        with patch('time.perf_counter', side_effect=[1, 1.25, 2, 2.5]):
            self.assertTrue(self.user.check_password(self.raw_password))
            self.assertFalse(self.user.check_password('private-wrong-password'))
        self.assertEqual(state['capture_metrics'], {'existing_seconds': 3.0,
            'password_check_count': 2, 'password_check_seconds': .75})
        self.assertEqual(self.user.password, self.encoded)
        serialized = json.dumps(state)
        for secret in (self.raw_password, 'private-wrong-password', self.user.username, self.encoded):
            self.assertNotIn(secret, serialized)

    def test_real_password_without_context_uses_native_path_without_reading_clock(self):
        self.timing_state(None)
        with patch('time.perf_counter') as clock:
            self.assertTrue(self.user.check_password(self.raw_password))
            self.assertFalse(self.user.check_password('private-wrong-password'))
        clock.assert_not_called()
        self.assertIsNone(pending_timing.get())

    def test_native_exception_identity_is_preserved_and_counted(self):
        state = {}
        self.timing_state(state)
        original = RuntimeError('private-native-error')
        with patch.object(AbstractUser, 'check_password', side_effect=original), \
             patch('time.perf_counter', side_effect=[3, 3.125]):
            with self.assertRaises(RuntimeError) as caught:
                self.user.check_password(self.raw_password)
        self.assertIs(caught.exception, original)
        self.assertEqual(state, {'capture_metrics': {'password_check_count': 1, 'password_check_seconds': .125}})
        self.assertNotIn('private', json.dumps(state))

    def test_native_exception_without_context_passes_through_without_reading_clock(self):
        self.timing_state(None)
        original = ValueError('private-native-error')
        with patch.object(AbstractUser, 'check_password', side_effect=original), \
             patch('time.perf_counter') as clock:
            with self.assertRaises(ValueError) as caught:
                self.user.check_password(self.raw_password)
        self.assertIs(caught.exception, original)
        clock.assert_not_called()

    def test_native_upgrade_setter_keeps_current_hash_strength_and_password_save(self):
        state = {}
        self.timing_state(state)
        hasher = get_hasher()
        original_iterations = getattr(hasher, 'iterations', None)
        # Request a native upgrade using the current-strength hash; do not use
        # a cheaper hasher or lower the work factor to speed up the fixture.
        with patch.object(type(hasher), 'must_update', return_value=True), \
             patch.object(self.user, 'save') as save, \
             patch('time.perf_counter', side_effect=[4, 4.5]):
            self.assertTrue(self.user.check_password(self.raw_password))
        save.assert_called_once_with(update_fields=['password'])
        self.assertIsNone(self.user._password)
        self.assertNotEqual(self.user.password, self.encoded)
        self.assertEqual(identify_hasher(self.user.password).algorithm, hasher.algorithm)
        if original_iterations is not None:
            self.assertEqual(hasher.decode(self.user.password)['iterations'], original_iterations)
        self.assertEqual(state, {'capture_metrics': {'password_check_count': 1, 'password_check_seconds': .5}})
