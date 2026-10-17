from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from apps.api.views import LoginApiView
from config.capture_timing import TIMING_ENV_KEY


class LoginTimingTests(SimpleTestCase):
    def test_login_records_authentication_session_and_projection_separately(self):
        request = APIRequestFactory().post('/api/v1/auth/login/',
            {'username': 'timing-student', 'password': 'not-logged'}, format='json')
        state = request.META[TIMING_ENV_KEY] = {}
        with patch('apps.api.views.enforce_csrf'), \
                patch('apps.api.views.authenticate', return_value=SimpleNamespace(pk=1)), \
                patch('apps.api.views.login'), \
                patch('apps.api.views.session_context', return_value={'user': {'id': 1}}), \
                patch('apps.api.views.get_token', return_value='csrf-not-logged'):
            response = LoginApiView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        metrics = state.get('capture_metrics', {})
        self.assertEqual(set(metrics), {'login_authenticate_seconds', 'login_session_seconds',
                                        'login_projection_seconds'})
        self.assertTrue(all(value >= 0 for value in metrics.values()))
        self.assertNotIn('not-logged', str(metrics))
