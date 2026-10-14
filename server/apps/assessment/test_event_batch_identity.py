"""Batch identity reads stay bounded while exact replay and race conflicts remain authoritative."""
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .event_batch_services import ProcessConflictError, append_event_batch
from .models import AssessmentEvent, ExperimentSession
from . import test_event_services as event_fixtures


class IdentityBatchFixture:
    setUp = event_fixtures.AssessmentEventAppendServiceTests.setUp

    def document(self, count=6, stream=None):
        stream = stream or uuid.uuid4()
        now = timezone.now().isoformat()
        return {'client_instance_id': str(stream), 'events': [
            {'event_id': str(uuid.uuid4()), 'event_type': 'item_rendered', 'event_version': 1,
             'session_id': self.session.pk, 'attempt_id': self.assignment.attempt.pk,
             'client_sequence_no': index, 'client_wall_time_utc': now,
             'client_monotonic_ms': index * 100, 'client_time_origin': now,
             'quality_flags': [], 'app_version': 'identity-batch', 'payload': {}}
            for index in range(1, count + 1)]}

    def ingest(self, document):
        return append_event_batch(student=self.student, document=document)


class BatchIdentityQueryTests(IdentityBatchFixture, TestCase):
    def identity_queries(self, document):
        with CaptureQueriesContext(connection) as queries:
            self.ingest(document)
        # Exclude monotonic predecessor and existence checks; those have separate semantics.
        return [row['sql'] for row in queries if row['sql'].startswith('SELECT')
                and 'FROM "assessment_assessmentevent"' in row['sql']
                and '"client_sequence_no" <' not in row['sql']
                and not row['sql'].startswith('SELECT 1 ')]

    def test_existing_identity_reads_are_one_query_for_one_or_many_new_events(self):
        one = len(self.identity_queries(self.document(1)))
        many = len(self.identity_queries(self.document(6)))
        self.assertEqual((one, many), (1, 1), 'Identity SELECTs must be batched, not repeated per event')

    def test_replay_identity_reads_are_one_query_for_one_or_many_events(self):
        document = self.document()
        self.ingest(document)
        self.assertEqual(len(self.identity_queries(document)), 1)
        self.assertEqual(len(self.identity_queries({**document, 'events': document['events'][:1]})), 1)

    def test_batch_marks_source_dirty_once_and_registers_one_callback(self):
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            status=ExperimentSession.Status.COMPLETED, terminal_at=timezone.now())
        self.session.refresh_from_db()
        before = self.session.research_source_revision
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.ingest(self.document(6))
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, before + 1)
        marked = [callback for callback in callbacks
                  if getattr(callback, '_coalesced_research_session_ids', ())]
        self.assertEqual(len(marked), 1)

    def test_exact_batch_replay_does_not_write_sequence_or_source_revision(self):
        document = self.document()
        original = self.ingest(document)
        self.session.refresh_from_db()
        before = (self.session.next_ingest_sequence_no, self.session.research_source_revision)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.ingest(document), original)
        writes = [row['sql'] for row in queries
                  if row['sql'].lstrip().startswith(('INSERT ', 'UPDATE ', 'DELETE '))]
        self.assertEqual(writes, [])
        self.session.refresh_from_db()
        self.assertEqual((self.session.next_ingest_sequence_no,
                          self.session.research_source_revision), before)

    def test_changed_uuid_and_original_material_still_conflict(self):
        document = self.document(1)
        original = self.ingest(document)
        for material in ('event_id', 'app_version', 'quality_flags', 'client_wall_time_utc'):
            with self.subTest(material=material):
                changed = copy.deepcopy(document)
                changed['events'][0][material] = (
                    str(uuid.uuid4()) if material == 'event_id' else 'changed' if material == 'app_version'
                    else ['offline_buffered'] if material == 'quality_flags' else timezone.now().isoformat())
                with self.assertRaises(ProcessConflictError):
                    self.ingest(changed)
                self.assertEqual(self.ingest(document), original)
        self.assertEqual(AssessmentEvent.objects.count(), 1)

    def test_uuid_collision_on_different_client_or_sequence_remains_conflict(self):
        document = self.document(1)
        self.ingest(document)
        for field in ('client', 'sequence'):
            with self.subTest(field=field):
                changed = copy.deepcopy(document)
                if field == 'client':
                    changed['client_instance_id'] = str(uuid.uuid4())
                else:
                    changed['events'][0]['client_sequence_no'] = 2
                with self.assertRaises(ProcessConflictError):
                    self.ingest(changed)
        self.assertEqual(AssessmentEvent.objects.count(), 1)

    def test_repeated_uuid_inside_batch_rolls_back_earlier_insert(self):
        document = self.document(2)
        document['events'][1]['event_id'] = document['events'][0]['event_id']
        with self.assertRaises(ProcessConflictError):
            self.ingest(document)
        self.assertFalse(AssessmentEvent.objects.exists())
        self.session.refresh_from_db()
        self.assertEqual(self.session.next_ingest_sequence_no, 1)


class BatchIdentityConcurrencyTests(IdentityBatchFixture, TransactionTestCase):
    def test_concurrent_identical_batch_replays_one_set_of_events_and_sequences(self):
        document = self.document()
        barrier = Barrier(2)
        def append():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.ingest(copy.deepcopy(document))
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = [future.result(timeout=30) for future in (pool.submit(append), pool.submit(append))]
        self.assertEqual(left, right)
        self.assertEqual(AssessmentEvent.objects.count(), 6)
        self.assertEqual(ExperimentSession.objects.get(pk=self.session.pk).next_ingest_sequence_no, 7)

    def test_prefetch_race_on_other_session_uses_live_conflict_lookup(self):
        from . import event_services
        from .models import _assessment_event_append_scope
        document = self.document(1)
        other_session = ExperimentSession.objects.create(version=self.session.version, student=self.teacher)
        loaded = Barrier(2)
        inserted = Barrier(2)
        original = event_services._resolve_idempotent_replay
        first = True
        def pause_after_miss(**kwargs):
            nonlocal first
            result = original(**kwargs)
            if first:
                first = False
                loaded.wait(timeout=10)
                inserted.wait(timeout=10)
            return result
        def competitor():
            close_old_connections()
            try:
                loaded.wait(timeout=10)
                with transaction.atomic(), _assessment_event_append_scope():
                    AssessmentEvent.objects.create(event_id=uuid.UUID(document['events'][0]['event_id']),
                        event_type='session_started', event_version=1, event_domain='assessment',
                        actor_source='server', session=other_session, student=self.teacher,
                        client_instance_id=uuid.UUID(document['client_instance_id']), client_sequence_no=1,
                        server_ingest_sequence_no=1, payload={}, app_version='racing-other-session')
                inserted.wait(timeout=10)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(event_services, '_resolve_idempotent_replay', side_effect=pause_after_miss):
            future = pool.submit(competitor)
            with self.assertRaises(ProcessConflictError):
                self.ingest(document)
            future.result(timeout=30)
        self.assertEqual(AssessmentEvent.objects.count(), 1)
        self.assertEqual(AssessmentEvent.objects.get().session_id, other_session.pk)
        self.assertEqual(ExperimentSession.objects.get(pk=self.session.pk).next_ingest_sequence_no, 1)
