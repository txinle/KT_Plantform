from datetime import datetime, timedelta, timezone as dt_timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.contrib.sessions.models import Session
from django.contrib.sessions.backends.db import SessionStore as DjangoSessionStore
from django.db import connection, connections, close_old_connections
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User


ENGINE = 'apps.accounts.database_sessions'


@override_settings(SESSION_ENGINE=ENGINE, CANONICAL_PUBLIC_ORIGIN='', STUDENT_SESSION_IDLE_TIMEOUT_SECONDS=1800)
class DatabaseIdleRenewalTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='database-idle-student', role=User.Role.STUDENT,
            student_number='DATABASE-IDLE-1', password='test-password',
        )
        self.client.force_login(self.student)
        self.key = self.client.session.session_key
        self.now = datetime(2030, 1, 1, 0, 0, 5, tzinfo=dt_timezone.utc)
        Session.objects.filter(session_key=self.key).update(expire_date=self.now + timedelta(seconds=60))

    def test_repeated_reads_in_one_window_issue_only_one_session_update(self):
        before = Session.objects.get(session_key=self.key).session_data
        with patch('django.utils.timezone.now', return_value=self.now), CaptureQueriesContext(connection) as queries:
            responses = [self.client.get('/api/v1/auth/session/') for _ in range(3)]
        self.assertEqual([response.status_code for response in responses], [200, 200, 200])
        updates = [query['sql'] for query in queries if query['sql'].startswith('UPDATE "django_session"')]
        self.assertEqual(len(updates), 1)
        self.assertNotIn('session_data', updates[0])
        row = Session.objects.get(session_key=self.key)
        self.assertEqual(row.session_data, before)
        self.assertEqual(row.expire_date, self.now.replace(second=30) + timedelta(seconds=1800))
        self.assertGreaterEqual(int(responses[-1].cookies[settings.SESSION_COOKIE_NAME]['max-age']), 1825)
        self.assertGreaterEqual(parsedate_to_datetime(responses[-1].cookies[settings.SESSION_COOKIE_NAME]['expires']), row.expire_date)

    def test_next_window_advances_expiry_and_keeps_active_student_authenticated(self):
        with patch('django.utils.timezone.now', return_value=self.now):
            self.assertEqual(self.client.get('/api/v1/auth/session/').status_code, 200)
        later = self.now + timedelta(seconds=1800)
        with patch('django.utils.timezone.now', return_value=later), CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get('/api/v1/auth/session/').status_code, 200)
        self.assertEqual(sum(query['sql'].startswith('UPDATE "django_session"') for query in queries), 1)
        self.assertEqual(Session.objects.get(session_key=self.key).expire_date,
                         later.replace(second=30) + timedelta(seconds=1800))

    def test_logout_during_renewal_removes_cookie_without_recreating_row(self):
        from apps.accounts.database_sessions import SessionStore
        original = SessionStore.touch_idle
        def logout_before_touch(store, timeout):
            Session.objects.filter(session_key=store.session_key).delete()
            return original(store, timeout)
        with patch('django.utils.timezone.now', return_value=self.now), patch.object(SessionStore, 'touch_idle', logout_before_touch):
            response = self.client.get('/api/v1/auth/session/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies[settings.SESSION_COOKIE_NAME]['max-age'], 0)
        self.assertFalse(Session.objects.filter(session_key=self.key).exists())

    def test_application_429_only_renews_once_and_reports_database_metrics(self):
        from apps.accounts.middleware import StudentSessionIdleTimeoutMiddleware
        from apps.accounts.session_middleware import SessionMiddleware
        from config.capture_timing import TIMING_ENV_KEY

        metrics = []
        with patch('django.utils.timezone.now', return_value=self.now), CaptureQueriesContext(connection) as queries:
            for _ in range(3):
                request = RequestFactory().get('/api/v1/student/tasks/1/start/')
                request.COOKIES[settings.SESSION_COOKIE_NAME] = self.key
                request.META[TIMING_ENV_KEY] = {}
                middleware = SessionMiddleware(lambda request: HttpResponse(status=429))
                middleware.process_request(request)
                self.assertEqual(request.session['_auth_user_id'], str(self.student.pk))
                request.user = self.student
                response = StudentSessionIdleTimeoutMiddleware(lambda request: HttpResponse(status=429))(request)
                middleware.process_response(request, response)
                metrics.append(request.META[TIMING_ENV_KEY]['capture_metrics'])
        self.assertEqual(sum(query['sql'].startswith('UPDATE "django_session"') for query in queries), 1)
        self.assertEqual(sum(metric['session_touch_renew_count'] for metric in metrics), 1)
        self.assertTrue(all(metric['session_backend'] == 'database' for metric in metrics))
        self.assertTrue(all(metric['session_save_count'] == 0 for metric in metrics))

    def test_real_login_saves_immediately_with_configured_idle_timeout(self):
        self.client.logout()
        with override_settings(STUDENT_SESSION_IDLE_TIMEOUT_SECONDS=60), patch('django.utils.timezone.now', return_value=self.now):
            response = self.client.post('/api/v1/auth/login/',
                {'username': self.student.username, 'password': 'test-password'}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        row = Session.objects.get(session_key=self.client.cookies[settings.SESSION_COOKIE_NAME].value)
        self.assertEqual(row.expire_date, self.now.replace(second=30) + timedelta(seconds=60))
        self.assertGreaterEqual(int(response.cookies[settings.SESSION_COOKIE_NAME]['max-age']), 85)


@override_settings(SESSION_ENGINE=ENGINE)
class DatabaseSessionStoreTests(TestCase):
    def setUp(self):
        from apps.accounts.database_sessions import SessionStore
        self.store_class = SessionStore
        self.now = datetime(2030, 1, 1, 0, 0, 5, tzinfo=dt_timezone.utc)
        self.original = DjangoSessionStore()
        self.original['value'] = 1
        self.original.save()
        self.key = self.original.session_key
        Session.objects.filter(session_key=self.key).update(expire_date=self.now + timedelta(seconds=60))

    def load_store(self):
        store = self.store_class(self.key)
        self.assertEqual(store['value'], 1)
        return store

    def test_existing_django_payload_load_and_expiry_use_one_query_and_keep_signing(self):
        with patch('django.utils.timezone.now', return_value=self.now), self.assertNumQueries(1):
            store = self.load_store()
        self.assertEqual(store.key_salt, self.original.key_salt)
        with patch('django.utils.timezone.now', return_value=self.now), self.assertNumQueries(1):
            ttl, renewed = store.touch_idle(1800)
        self.assertTrue(renewed)
        self.assertEqual(ttl, 1825000)
        self.assertFalse(store.modified)
        self.assertEqual(DjangoSessionStore(self.key)['value'], 1)

    def test_covered_expiry_needs_no_query_and_never_shortens(self):
        expiry = self.now + timedelta(days=1)
        Session.objects.filter(session_key=self.key).update(expire_date=expiry)
        with patch('django.utils.timezone.now', return_value=self.now):
            store = self.load_store()
            with self.assertNumQueries(0):
                ttl, renewed = store.touch_idle(1800)
        self.assertFalse(renewed)
        self.assertEqual(ttl, 86400000)
        self.assertEqual(Session.objects.get(session_key=self.key).expire_date, expiry)

    def test_inflight_renewal_cannot_revive_deleted_or_expired_row(self):
        for deleted in (False, True):
            with self.subTest(deleted=deleted), patch('django.utils.timezone.now', return_value=self.now):
                store = self.load_store()
                if deleted:
                    Session.objects.filter(session_key=self.key).delete()
                else:
                    Session.objects.filter(session_key=self.key).update(expire_date=self.now)
                self.assertEqual(store.touch_idle(1800), (-2, False))
            if not deleted:
                self.assertEqual(Session.objects.get(session_key=self.key).expire_date, self.now)
                Session.objects.filter(session_key=self.key).update(expire_date=self.now + timedelta(seconds=60))
        self.assertFalse(Session.objects.filter(session_key=self.key).exists())

    def test_payload_save_and_rotation_are_immediate_and_old_key_is_deleted(self):
        with patch('django.utils.timezone.now', return_value=self.now):
            store = self.load_store()
            store.student_idle_timeout = 60
            store['value'] = 2
            store.save()
            self.assertEqual(DjangoSessionStore(self.key)['value'], 2)
            store.cycle_key()
            self.assertFalse(Session.objects.filter(session_key=self.key).exists())
            self.assertEqual(DjangoSessionStore(store.session_key)['value'], 2)
            store.flush()
            self.assertIsNone(store.student_idle_timeout)

    def test_inactive_student_is_rejected_at_bucket_expiry(self):
        with patch('django.utils.timezone.now', return_value=self.now):
            store = self.load_store()
            store.touch_idle(60)
        expiry = self.now.replace(second=30) + timedelta(seconds=60)
        self.assertLessEqual((expiry - self.now).total_seconds() - 60, 30)
        with patch('django.utils.timezone.now', return_value=expiry):
            self.assertEqual(self.store_class(self.key).load(), {})


@skipUnless(connection.vendor == 'postgresql', 'requires PostgreSQL compare-and-set semantics')
@override_settings(SESSION_ENGINE=ENGINE)
class PostgreSQLSessionRenewalTests(TransactionTestCase):
    def test_same_bucket_parallel_requests_only_update_one_row(self):
        from apps.accounts.database_sessions import SessionStore
        now = datetime(2030, 1, 1, 0, 0, 5, tzinfo=dt_timezone.utc)
        store = DjangoSessionStore()
        store['value'] = 1
        store.save()
        Session.objects.filter(session_key=store.session_key).update(expire_date=now + timedelta(seconds=60))
        barrier = Barrier(8)

        def renew(_):
            close_old_connections()
            effective_updates = []
            def count_update(execute, sql, params, many, context):
                result = execute(sql, params, many, context)
                if sql.startswith('UPDATE "django_session"'):
                    effective_updates.append(context['cursor'].rowcount)
                return result
            try:
                current = SessionStore(store.session_key)
                self.assertEqual(current['value'], 1)
                barrier.wait(timeout=10)
                with connections['default'].execute_wrapper(count_update):
                    result = current.touch_idle(1800)
                return result, effective_updates
            finally:
                connections['default'].close()

        with patch('django.utils.timezone.now', return_value=now), ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(renew, range(8)))
        self.assertEqual(sum(result[0][1] for result in results), 1)
        self.assertEqual(sum(sum(result[1]) for result in results), 1)
        self.assertEqual(Session.objects.get(session_key=store.session_key).expire_date,
                         now.replace(second=30) + timedelta(seconds=1800))
