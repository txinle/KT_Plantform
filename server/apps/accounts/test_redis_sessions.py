import importlib
import json
import os
import runpy
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import redis
from django.conf import settings
from django.contrib.sessions.backends.base import CreateError, UpdateError
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from apps.accounts.models import User
from apps.api.test_support import domain_payload


ENGINE = 'apps.accounts.redis_sessions'


class RedisSessionBoundaryTests(SimpleTestCase):
    def test_diagnostics_wraps_session_response(self):
        self.assertIn('apps.accounts.session_middleware.SessionMiddleware', settings.MIDDLEWARE)
        self.assertLess(settings.MIDDLEWARE.index('apps.api.middleware.RequestIdMiddleware'),
                        settings.MIDDLEWARE.index('apps.accounts.session_middleware.SessionMiddleware'))

    @override_settings(SESSION_ENGINE=ENGINE, SESSION_REDIS_URL='redis://127.0.0.1:1/0')
    def test_read_outage_is_explicit_503_without_secret_or_database_fallback(self):
        self.assertIsNotNone(importlib.util.find_spec(ENGINE), 'Redis session engine is missing')
        client = Client()
        client.cookies[settings.SESSION_COOKIE_NAME] = 'a' * 32
        response = client.get('/api/v1/auth/session/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'SESSION_STORE_UNAVAILABLE')
        self.assertNotIn('127.0.0.1', response.content.decode())
        self.assertNotIn('a' * 32, response.content.decode())

    @override_settings(SESSION_ENGINE=ENGINE, SESSION_REDIS_URL='redis://127.0.0.1:1/0')
    def test_response_save_outage_reports_possible_committed_business_outcome(self):
        self.assertIsNotNone(importlib.util.find_spec('apps.accounts.session_middleware'), 'Session failure boundary is missing')
        from apps.accounts.session_middleware import SessionMiddleware
        request = RequestFactory().post('/api/v1/student/attempts/1/draft/')
        def view(request):
            request.session['value'] = 'private answer'
            return HttpResponse('committed')
        response = SessionMiddleware(view)(request)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(json.loads(response.content)['error']['fields']['business_outcome_unknown'])
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_engine_has_no_automatic_transport_retries(self):
        from apps.accounts.redis_sessions import session_client
        self.assertIn('retry', session_client('redis://127.0.0.1:1/0', 1).connection_pool.connection_kwargs)
        self.assertEqual(session_client('redis://127.0.0.1:1/0', 1).connection_pool.connection_kwargs['retry']._retries, 0)

    def test_opt_in_requires_explicit_school_and_isolated_database(self):
        from django.core.exceptions import ImproperlyConfigured
        path = Path(settings.BASE_DIR) / 'config' / 'settings.py'
        for flag, database in [('0', 'kt_e2e'), ('1', 'kt_platform')]:
            with patch.dict(os.environ, {'SCHOOL_REDIS_SESSION_ENABLED': '1',
                                         'SCHOOL_RUNTIME_ISOLATION_ENABLED': flag, 'POSTGRES_DB': database}):
                with self.assertRaises(ImproperlyConfigured):
                    runpy.run_path(str(path))
        with patch.dict(os.environ, {'SCHOOL_REDIS_SESSION_ENABLED': '1',
                                     'SCHOOL_RUNTIME_ISOLATION_ENABLED': '1', 'POSTGRES_DB': 'kt_e2e'}):
            candidate = runpy.run_path(str(path))
        self.assertEqual(candidate['SESSION_ENGINE'], ENGINE)
        self.assertNotIn('SESSION_COOKIE_NAME', candidate)
        self.assertEqual(settings.SESSION_COOKIE_NAME, 'sessionid')
        with patch.dict(os.environ, {'SCHOOL_REDIS_SESSION_ENABLED': '0', 'POSTGRES_DB': 'kt_platform'}):
            original = runpy.run_path(str(path))
        self.assertEqual(original['SESSION_ENGINE'], 'apps.accounts.database_sessions')

    def test_school_compose_uses_one_durable_bounded_session_service(self):
        import yaml
        loader = type('SessionLoader', (yaml.SafeLoader,), {})
        loader.add_constructor('!reset', lambda loader, node: loader.construct_sequence(node))
        loader.add_constructor('!override', lambda loader, node: loader.construct_sequence(node))
        body = yaml.load((Path(settings.REPOSITORY_DIR) / 'deploy' / 'docker-compose.school.yml').read_text(), Loader=loader)
        service = body['services']['redis-session']
        self.assertEqual(service['image'], 'redis:7.4-alpine')
        self.assertEqual(service['profiles'], ['school-redis-session'])
        self.assertEqual(service['mem_limit'], '512M')
        self.assertEqual(service['cpus'], 1)
        self.assertIn('always', service['command'])
        self.assertIn('noeviction', service['command'])
        self.assertIn('256mb', service['command'])
        self.assertEqual(service['volumes'], ['redis_session_data:/data'])
        for name in ['web', 'student-read', 'teacher-read']:
            env = body['services'][name]['environment']
            self.assertEqual(env['SESSION_REDIS_URL'], 'redis://redis-session:6379/0')
            self.assertEqual(env['SCHOOL_REDIS_SESSION_ENABLED'], '${SCHOOL_REDIS_SESSION_ENABLED:-0}')
            self.assertFalse(body['services'][name]['depends_on']['redis-session']['required'])
        candidate = yaml.load((Path(settings.REPOSITORY_DIR) / 'deploy' / 'docker-compose.school-test.yml').read_text(), Loader=loader)
        for name in ['web', 'student-read', 'teacher-read']:
            self.assertEqual(candidate['services'][name]['environment']['SESSION_REDIS_TIMEOUT_SECONDS'], '${SESSION_REDIS_TIMEOUT_SECONDS:-5}')


@skipUnless(os.getenv('KT_SESSION_TEST_REDIS_URL'), 'requires explicit isolated real Redis')
@override_settings(SESSION_ENGINE=ENGINE)
class RealRedisSessionTests(SimpleTestCase):
    def setUp(self):
        self.prefix = 'kt-session-test:' + uuid.uuid4().hex + ':'
        self.override = override_settings(SESSION_REDIS_URL=os.environ['KT_SESSION_TEST_REDIS_URL'],
                                         SESSION_REDIS_KEY_PREFIX=self.prefix)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.store_class = importlib.import_module(ENGINE).SessionStore
        self.redis = redis.Redis.from_url(settings.SESSION_REDIS_URL, decode_responses=True)
        self.addCleanup(self.cleanup_keys)

    def cleanup_keys(self):
        keys = list(self.redis.scan_iter(match=self.prefix + '*'))
        if keys:
            self.redis.delete(*keys)

    def test_django_signed_roundtrip_and_atomic_expiry(self):
        session = self.store_class()
        session['answer'] = 'PRIVATE'
        session.set_expiry(1800)
        session.save()
        key = self.prefix + session.session_key
        raw = self.redis.get(key)
        self.assertEqual(session.decode(raw)['answer'], 'PRIVATE')
        self.assertEqual(self.store_class(session.session_key)['answer'], 'PRIVATE')
        self.assertGreaterEqual(self.redis.ttl(key), 1798)
        self.assertLessEqual(self.redis.ttl(key), 1800)
        session.set_expiry(600)
        session.save()
        self.assertLessEqual(self.redis.ttl(key), 600)

    def test_logout_does_not_resurrect_an_inflight_session(self):
        session = self.store_class()
        session['value'] = 1
        session.save()
        inflight = self.store_class(session.session_key)
        self.assertEqual(inflight['value'], 1)
        session.flush()
        inflight['value'] = 2
        with self.assertRaises(UpdateError):
            inflight.save()
        self.assertFalse(self.redis.exists(self.prefix + inflight.session_key))

    def test_create_collision_does_not_overwrite(self):
        session = self.store_class()
        session['value'] = 1
        session.save()
        duplicate = self.store_class(session.session_key)
        duplicate['value'] = 2
        with self.assertRaises(CreateError):
            duplicate.save(must_create=True)
        self.assertEqual(self.store_class(session.session_key)['value'], 1)

    def test_expired_key_is_anonymous_and_cannot_be_updated(self):
        session = self.store_class()
        session['value'] = 1
        session.save()
        self.redis.delete(self.prefix + session.session_key)
        with self.assertRaises(UpdateError):
            session.save()
        expired = self.store_class(session.session_key)
        self.assertEqual(expired.load(), {})
        self.assertIsNone(expired.session_key)

    def test_key_rotation_preserves_data_and_removes_old_key(self):
        session = self.store_class()
        session['value'] = 1
        session.save()
        old_key = session.session_key
        session.cycle_key()
        self.assertNotEqual(session.session_key, old_key)
        self.assertFalse(session.exists(old_key))
        self.assertEqual(self.store_class(session.session_key)['value'], 1)

    def test_idle_touch_only_writes_at_the_threshold_and_preserves_signed_payload(self):
        session = self.store_class()
        session['value'] = 1
        session.set_expiry(1830)
        session.save()
        key = self.prefix + session.session_key
        signed = self.redis.get(key)
        self.redis.pexpire(key, 1810000)
        untouched, renewed = session.touch_idle(1800)
        self.assertFalse(renewed)
        self.assertGreater(untouched, 1800000)
        self.assertLessEqual(untouched, 1810000)
        self.redis.pexpire(key, 1800000)
        renewed_ttl, renewed = session.touch_idle(1800)
        self.assertEqual(renewed_ttl, 1830000)
        self.assertTrue(renewed)
        self.assertEqual(self.redis.get(key), signed)
        loaded = self.store_class(session.session_key)
        loaded.load()
        loaded.touch_idle(1800)
        self.assertFalse(loaded.modified)

    def test_concurrent_idle_touches_renew_exactly_once(self):
        session = self.store_class()
        session['value'] = 1
        session.save()
        key = self.prefix + session.session_key
        self.redis.pexpire(key, 100000)
        with ThreadPoolExecutor(max_workers=8) as executor:
            touches = list(executor.map(lambda _: self.store_class(session.session_key).touch_idle(1800), range(16)))
        self.assertEqual(sum(renewed for _, renewed in touches), 1)
        self.assertTrue(all(1800000 < ttl <= 1830000 for ttl, _ in touches))

    def test_idle_touch_never_recreates_a_key_deleted_by_another_pool(self):
        session = self.store_class()
        session['value'] = 1
        session.save()
        inflight = self.store_class(session.session_key)
        self.assertEqual(inflight['value'], 1)
        session.flush()
        self.assertEqual(inflight.touch_idle(1800), (-2, False))
        self.assertFalse(self.redis.exists(self.prefix + inflight.session_key))


@skipUnless(os.getenv('KT_SESSION_TEST_REDIS_URL'), 'requires explicit isolated real Redis')
@override_settings(SESSION_ENGINE=ENGINE, CANONICAL_PUBLIC_ORIGIN='')
class RedisAuthenticationTests(TestCase):
    def setUp(self):
        self.override = override_settings(SESSION_REDIS_URL=os.environ['KT_SESSION_TEST_REDIS_URL'],
                                         SESSION_REDIS_KEY_PREFIX='kt-session-auth-test:' + uuid.uuid4().hex + ':')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.redis = redis.Redis.from_url(settings.SESSION_REDIS_URL)
        self.addCleanup(self.cleanup_keys)

    def cleanup_keys(self):
        keys = list(self.redis.scan_iter(match=settings.SESSION_REDIS_KEY_PREFIX + '*'))
        if keys:
            self.redis.delete(*keys)

    def test_csrf_login_sliding_student_expiry_teacher_expiry_and_logout(self):
        student = User.objects.create_user(username='redis-student', password='test-password',
                                          role=User.Role.STUDENT, student_number='REDIS-1')
        teacher = User.objects.create_user(username='redis-teacher', password='test-password', role=User.Role.TEACHER)
        client = Client(enforce_csrf_checks=True)
        csrf = domain_payload(client.get('/api/v1/auth/csrf/'))['csrf_token']
        payload = json.dumps({'username': student.username, 'password': 'test-password'})
        self.assertEqual(client.post('/api/v1/auth/login/', payload, content_type='application/json').status_code, 403)
        self.assertEqual(client.post('/api/v1/auth/login/', payload, content_type='application/json', HTTP_X_CSRFTOKEN=csrf).status_code, 200)
        session = client.session
        session.set_expiry(60)
        session.save()
        self.assertEqual(client.get('/api/v1/auth/session/').status_code, 200)
        self.assertGreater(self.redis.ttl(settings.SESSION_REDIS_KEY_PREFIX + client.session.session_key), 1800)
        self.assertLessEqual(self.redis.ttl(settings.SESSION_REDIS_KEY_PREFIX + client.session.session_key), 1830)
        old_key = client.session.session_key
        client.logout()
        self.assertFalse(self.redis.exists(settings.SESSION_REDIS_KEY_PREFIX + old_key))
        self.assertEqual(client.get('/api/v1/auth/session/').status_code, 401)
        client.force_login(teacher)
        session = client.session
        session.set_expiry(600)
        session.save()
        self.assertEqual(client.get('/api/v1/auth/session/').status_code, 200)
        self.assertLessEqual(client.session.get_expiry_age(), 600)

    def test_student_read_touches_ttl_without_resaving_session_and_cookie_matches_remaining_ttl(self):
        student = User.objects.create_user(username='ttl-student', password='test-password',
                                          role=User.Role.STUDENT, student_number='TTL-1')
        client = Client()
        client.force_login(student)
        key = settings.SESSION_REDIS_KEY_PREFIX + client.session.session_key
        self.redis.pexpire(key, 1810000)
        signed = self.redis.get(key)
        from apps.accounts.redis_sessions import SessionStore
        with patch.object(SessionStore, 'save', autospec=True, wraps=SessionStore.save) as save:
            response = client.get('/api/v1/auth/bootstrap/')
        self.assertEqual(response.status_code, 200)
        save.assert_not_called()
        self.assertEqual(self.redis.get(key), signed)
        self.assertGreater(self.redis.ttl(key), 1800)
        cookie_age = int(response.cookies[settings.SESSION_COOKIE_NAME]['max-age'])
        self.assertLessEqual(cookie_age, 1810)
        self.assertGreaterEqual(cookie_age, 1808)
        self.redis.pexpire(key, 100000)
        response = client.get('/api/v1/auth/bootstrap/')
        self.assertGreaterEqual(self.redis.ttl(key), 1828)
        self.assertEqual(self.redis.get(key), signed)
        self.assertLessEqual(int(response.cookies[settings.SESSION_COOKIE_NAME]['max-age']), 1830)

    def test_student_login_persists_idle_grace_and_rotated_csrf_then_cross_pool_logout(self):
        student = User.objects.create_user(username='ttl-login', password='test-password',
                                          role=User.Role.STUDENT, student_number='TTL-LOGIN')
        client = Client(enforce_csrf_checks=True)
        token = domain_payload(client.get('/api/v1/auth/bootstrap/'))['csrf_token']
        response = client.post('/api/v1/auth/login/', json.dumps({'username': student.username, 'password': 'test-password'}),
                               content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        key = settings.SESSION_REDIS_KEY_PREFIX + client.session.session_key
        self.assertGreaterEqual(self.redis.ttl(key), 1828)
        self.assertEqual(client.session.get_expiry_age(), 1830)
        self.assertEqual(int(response.cookies[settings.SESSION_COOKIE_NAME]['max-age']), 1830)
        other_pool = Client(enforce_csrf_checks=True)
        other_pool.cookies = client.cookies.copy()
        self.assertEqual(other_pool.post('/api/v1/auth/logout/',
                         HTTP_X_CSRFTOKEN=domain_payload(response)['csrf_token']).status_code, 204)
        self.assertFalse(self.redis.exists(key))
        self.assertIsNone(domain_payload(client.get('/api/v1/auth/bootstrap/'))['user'])

    def test_student_payload_mutation_saves_immediately_with_idle_ttl(self):
        from apps.accounts.redis_sessions import SessionStore
        session = SessionStore()
        session.student_idle_timeout = 1800
        session['value'] = 1
        session.save()
        key = settings.SESSION_REDIS_KEY_PREFIX + session.session_key
        session['value'] = 2
        session.save()
        self.assertEqual(SessionStore(session.session_key)['value'], 2)
        self.assertGreaterEqual(self.redis.ttl(key), 1828)
        self.assertLessEqual(self.redis.ttl(key), 1830)

    def test_idle_touch_outage_is_503_and_does_not_claim_logout(self):
        student = User.objects.create_user(username='ttl-outage', password='test-password',
                                          role=User.Role.STUDENT, student_number='TTL-OUTAGE')
        self.client.force_login(student)
        from apps.accounts.redis_sessions import SessionStoreUnavailable
        with patch('apps.accounts.redis_sessions.SessionStore.touch_idle',
                   side_effect=SessionStoreUnavailable('eval'), create=True):
            response = self.client.get('/api/v1/auth/bootstrap/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'SESSION_STORE_UNAVAILABLE')
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_password_change_rotates_the_key_and_immediately_persists_auth_hash(self):
        student = User.objects.create_user(username='ttl-password', password='test-password',
                                          role=User.Role.STUDENT, student_number='TTL-PASSWORD')
        self.client.force_login(student)
        old_key = self.client.session.session_key
        response = self.client.post('/api/v1/auth/password/change/',
                                    json.dumps({'current_password': 'test-password',
                                                'new_password1': 'Changed-Unique-Pass-93',
                                                'new_password2': 'Changed-Unique-Pass-93'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        new_session = self.client.session
        self.assertNotEqual(new_session.session_key, old_key)
        self.assertFalse(self.redis.exists(settings.SESSION_REDIS_KEY_PREFIX + old_key))
        student.refresh_from_db()
        self.assertEqual(new_session['_auth_user_hash'], student.get_session_auth_hash())
        self.assertGreaterEqual(self.redis.ttl(settings.SESSION_REDIS_KEY_PREFIX + new_session.session_key), 1828)
        self.assertEqual(self.client.get('/api/v1/auth/bootstrap/').status_code, 200)

    def test_logout_during_a_read_response_deletes_cookie_without_recreating_key(self):
        from apps.accounts.redis_sessions import SessionStore
        student = User.objects.create_user(username='ttl-concurrent-logout', password='test-password',
                                          role=User.Role.STUDENT, student_number='TTL-CONCURRENT')
        self.client.force_login(student)
        old_key = self.client.session.session_key
        original = SessionStore.touch_idle
        def logout_before_touch(store, timeout):
            self.redis.delete(settings.SESSION_REDIS_KEY_PREFIX + store.session_key)
            return original(store, timeout)
        with patch.object(SessionStore, 'touch_idle', logout_before_touch):
            response = self.client.get('/api/v1/auth/bootstrap/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies[settings.SESSION_COOKIE_NAME]['max-age'], 0)
        self.assertFalse(self.redis.exists(settings.SESSION_REDIS_KEY_PREFIX + old_key))

    def test_logging_in_as_teacher_from_student_session_does_not_inherit_idle_policy(self):
        student = User.objects.create_user(username='ttl-switch-student', password='test-password',
                                          role=User.Role.STUDENT, student_number='TTL-SWITCH')
        teacher = User.objects.create_user(username='ttl-switch-teacher', password='test-password', role=User.Role.TEACHER)
        self.client.force_login(student)
        response = self.client.post('/api/v1/auth/login/',
                                    json.dumps({'username': teacher.username, 'password': 'test-password'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        key = settings.SESSION_REDIS_KEY_PREFIX + self.client.session.session_key
        self.assertGreater(self.redis.ttl(key), 1830)
