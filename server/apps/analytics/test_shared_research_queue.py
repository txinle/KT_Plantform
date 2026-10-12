from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import transaction
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import test_teacher_data_center_api as fixtures


@override_settings(ADVANCED_DATA_MATERIALIZED_ENABLED=True, ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({'*'}))
class SharedResearchQueueTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session

    def test_legacy_insert_can_omit_new_queue_columns(self):
        import uuid
        from .research_materialization import enqueue_session_materialization
        from .models import ResearchSessionMaterialization
        item = enqueue_session_materialization(self.session)
        item.status = item.Status.FAILED
        item.save(update_fields=['status'])
        columns = [field.column for field in item._meta.concrete_fields
                   if field.name not in ('materialization_id', 'next_attempt_at', 'priority')]
        quoted = ', '.join(connection.ops.quote_name(column) for column in columns)
        new_id = uuid.uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO analytics_researchsessionmaterialization ({quoted}, materialization_id) '
                f'SELECT {quoted}, %s FROM analytics_researchsessionmaterialization WHERE materialization_id = %s',
                [new_id, item.pk],
            )
        inserted = ResearchSessionMaterialization.objects.get(pk=new_id)
        self.assertEqual(inserted.priority, 0)
        self.assertIsNotNone(inserted.next_attempt_at)

    def test_recent_completion_discovered_after_missed_notification_is_high_priority(self):
        from .research_materialization_jobs import reconcile_session_materializations
        self.assertEqual(reconcile_session_materializations(limit=10)['enqueued'], 1)
        self.assertEqual(self.session.research_materializations.get().priority, 0)

    def test_historical_version_backfill_is_lower_priority_than_recent_results(self):
        from .research_materialization import enqueue_session_materialization
        from .research_materialization_jobs import reconcile_session_materializations
        old = enqueue_session_materialization(self.session)
        old.status = old.Status.SUCCEEDED
        old.is_current = True
        old.projection_version = 'research-session-v3'
        old.save(update_fields=['status', 'is_current', 'projection_version'])
        self.session.terminal_at = timezone.now() - timedelta(days=10)
        self.session.save(update_fields=['terminal_at'])
        self.assertEqual(reconcile_session_materializations(limit=10)['enqueued'], 1)
        self.assertEqual(self.session.research_materializations.get(status='queued').priority, 100)

    def test_old_pending_projection_is_retained_but_never_generated(self):
        from .research_materialization import enqueue_session_materialization, claim_session_materialization, PROJECTION_VERSION
        from .research_materialization_jobs import reconcile_session_materializations
        old = enqueue_session_materialization(self.session)
        old.projection_version = 'research-session-v3'
        old.save(update_fields=['projection_version'])
        self.assertIsNone(claim_session_materialization(worker_id='new-worker'))
        old.refresh_from_db()
        self.assertEqual(old.status, old.Status.FAILED)
        self.assertIn('projection version', old.error)
        self.assertEqual(reconcile_session_materializations(limit=10)['enqueued'], 1)
        self.assertEqual(claim_session_materialization(worker_id='new-worker').projection_version, PROJECTION_VERSION)
        self.assertEqual(self.session.research_materializations.count(), 2)
    def test_failed_revision_is_not_recreated_by_reconciliation(self):
        from .research_materialization import enqueue_session_materialization
        from .research_materialization_jobs import reconcile_session_materializations
        item = enqueue_session_materialization(self.session)
        item.status = item.Status.FAILED
        item.retry_count = 3
        item.save(update_fields=['status', 'retry_count'])
        for _ in range(3):
            self.assertEqual(reconcile_session_materializations(limit=10)['enqueued'], 0)
        self.assertEqual(self.session.research_materializations.count(), 1)
        self.session.research_source_revision += 1
        self.session.save(update_fields=['research_source_revision'])
        self.assertEqual(reconcile_session_materializations(limit=10)['enqueued'], 1)

    def test_deterministic_failure_is_terminal_and_explicit_retry_is_possible(self):
        from .research_materialization import enqueue_session_materialization, claim_session_materialization, _store_failure
        from .services import ExportContractError
        item = enqueue_session_materialization(self.session)
        claim_session_materialization(worker_id='a')
        failed = _store_failure(item.pk, worker_id='a', error=ExportContractError('invalid identity'))
        self.assertEqual(failed.status, failed.Status.FAILED)
        self.assertEqual(enqueue_session_materialization(self.session).pk, failed.pk)
        self.assertNotEqual(enqueue_session_materialization(self.session, force_retry=True).pk, failed.pk)

    def test_transient_failure_waits_before_next_claim(self):
        from .research_materialization import enqueue_session_materialization, claim_session_materialization, _store_failure
        item = enqueue_session_materialization(self.session)
        claim_session_materialization(worker_id='a')
        queued = _store_failure(item.pk, worker_id='a', error=RuntimeError('temporary source race'))
        self.assertEqual(queued.status, queued.Status.QUEUED)
        self.assertIsNone(claim_session_materialization(worker_id='b'))
        self.assertIsNotNone(claim_session_materialization(worker_id='b', now=timezone.now()+timedelta(seconds=31)))

    def test_revision_is_committed_even_if_queue_notification_fails(self):
        from .research_materialization_jobs import schedule_session_materialization
        with patch('apps.analytics.research_materialization_jobs._enqueue_if_eligible', side_effect=RuntimeError('offline')):
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    schedule_session_materialization(self.session.pk)
                    self.session.refresh_from_db()
                    self.assertEqual(self.session.research_source_revision, 1)
                    self.assertIsNotNone(getattr(self.session, 'research_source_changed_at', None))


class ResearchIdentityTests(SimpleTestCase):
    def test_bank_local_duplicate_codes_keep_distinct_snapshot_identity(self):
        from .services import _validate_question_codes
        assignments = [SimpleNamespace(snapshot=SimpleNamespace(pk=i, version_id=1, code='Q001', source_bank_id=i)) for i in (1, 2)]
        _validate_question_codes(assignments)
