import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from apps.question_bank.models import Question
from . import test_capture_v2 as capture
from .event_batch_services import ProcessRequestError
from .models import (
    AppendOnlyEventError, AssessmentEvent, CaptureMutationReceipt, ResponseInputObservation,
    _response_observation_append_scope,
)
from .response_input_services import record_stable_input_observation


class InputObservationFixture:
    transition = capture.CaptureV2Tests.transition
    ingest = capture.CaptureV2Tests.ingest

    def setUp(self):
        capture.CaptureV2Tests.setUp(self)
        self.stream = uuid.uuid4()
        self.attempt = self.attempts[Question.Type.SHORT]

    def edit(self, sequence, *, legacy=False):
        event = self.transition(self.attempt, '', 'A', sequence=sequence)
        event.update(event_type='text_edit_observed', event_version=1 if legacy else 2)
        event['payload'] = {
            'question_type': 'short_answer', 'edit_operation': 'insert',
            'content_hash_before': 'a' * 64, 'content_hash_after': 'b' * 64,
            'content_length_before': 0, 'content_length_after': 1, 'input_method': 'ime',
        }
        if not legacy:
            event['payload'].update(is_ime=True, batch_start_client_monotonic_ms=1.234567,
                                    batch_end_client_monotonic_ms=2.345678)
        return event

    def observation_values(self, sequence):
        return dict(attempt=self.attempt, client_instance_id=self.stream, client_sequence_no=sequence,
                    client_monotonic_ms=sequence * 100, edit_operation='insert',
                    content_hash_before='a' * 64, content_hash_after='b' * 64,
                    content_length_before=0, content_length_after=1, input_method='ime', is_ime=True)


class InputObservationBatchTests(InputObservationFixture, TestCase):
    def ingest_counted(self, events, *, reads, writes):
        with CaptureQueriesContext(connection) as queries:
            receipt = self.ingest(events)
        table = connection.ops.quote_name(ResponseInputObservation._meta.db_table)
        statements = [query['sql'].split()[0] for query in queries if table in query['sql']]
        self.assertEqual(statements, ['SELECT'] * reads + ['INSERT'] * writes)
        return receipt

    def test_query_shapes_and_replay(self):
        self.ingest_counted([self.transition(self.attempt, '', 'A')], reads=0, writes=0)
        sequence = 2
        for count in (1, 3, 12):
            with self.subTest(count=count):
                events = [self.edit(i) for i in range(sequence, sequence + count)]
                sequence += count
                receipt = self.ingest_counted(events, reads=1, writes=1)
                self.assertEqual(receipt, self.ingest_counted(events, reads=1, writes=0))
                saved = list(ResponseInputObservation.objects.filter(
                    client_sequence_no__in=[row['client_sequence_no'] for row in events]))
                self.assertEqual([str(row.source_event_id) for row in saved], [e['event_id'] for e in events])
                self.assertTrue(all(row.is_ime and row.batch_start_client_monotonic_ms == Decimal('1.235')
                                    and row.batch_end_client_monotonic_ms == Decimal('2.346') for row in saved))

    def test_legacy_null_source_and_mixed_replay(self):
        legacy = record_stable_input_observation(**self.observation_values(1))
        events = [self.edit(1, legacy=True), self.edit(2)]
        self.ingest_counted(events, reads=1, writes=1)
        self.ingest_counted(events, reads=1, writes=0)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.source_event_id)
        self.assertIsNone(legacy.batch_start_client_monotonic_ms)
        self.assertTrue(legacy.is_ime)

    def test_lookup_only_reads_requested_attempt_client_sequence_tuples(self):
        from .response_input_services import _append_input_observations
        second_client = uuid.uuid4()
        second_attempt = self.attempts[Question.Type.FILL_BLANK]
        keys = [(self.attempt, self.stream, 1), (second_attempt, second_client, 2)]
        for attempt, client, seq in [(self.attempt, second_client, 2), (second_attempt, self.stream, 1),
                                     (self.attempt, self.stream, 2)]:
            record_stable_input_observation(**{**self.observation_values(seq), 'attempt': attempt,
                                               'client_instance_id': client})
        rows = [ResponseInputObservation(**{**self.observation_values(seq), 'attempt': attempt,
                                             'client_instance_id': client}) for attempt, client, seq in keys]
        with patch.object(ResponseInputObservation, 'from_db', wraps=ResponseInputObservation.from_db) as loaded:
            _append_input_observations(rows, bulk=True)
        loaded.assert_not_called()
        self.assertEqual(ResponseInputObservation.objects.count(), 5)

    def test_conflicting_observation_rolls_back_new_events_and_snapshots(self):
        from .capture_bundle_services import save_draft_capture_bundle
        existing = self.observation_values(3)
        existing['content_length_after'] = 2
        record_stable_input_observation(**existing)
        transition = self.transition(self.attempt, '', 'A', sequence=2)
        events = [self.edit(1), transition, self.edit(3, legacy=True)]
        token = str(uuid.uuid4())
        document = {'target_event_id': transition['event_id'], 'expected_version': 0,
                    'capture_operation_id': token, 'capture_bundle': {
                        'version': 1, 'client_instance_id': str(self.stream),
                        'event_boundary': 3, 'events': events, 'checkpoints': []}}
        with self.assertRaisesMessage(ProcessRequestError, 'invalid_event'):
            save_draft_capture_bundle(student=self.student, attempt_id=self.attempt.pk, document=document)
        self.assertFalse(AssessmentEvent.objects.filter(event_id__in=[e['event_id'] for e in events]).exists())
        self.assertFalse(self.attempt.response_snapshots.exists())
        self.assertEqual(ResponseInputObservation.objects.count(), 1)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.response_version, 0)
        self.assertIsNone(self.attempt.active_response_event_id)
        self.assertIsNone(self.attempt.current_response_snapshot_id)
        rejection = CaptureMutationReceipt.objects.get(token=token).receipt
        self.assertEqual(rejection['outcome'], 'rejected')
        self.assertEqual(rejection['error']['code'], 'invalid_event')

    def test_manager_requires_scope_valid_fields_and_valid_foreign_keys(self):
        row = ResponseInputObservation(**self.observation_values(1))
        with self.assertRaises(AppendOnlyEventError):
            ResponseInputObservation.objects.bulk_create([row])
        with _response_observation_append_scope():
            for option in ('ignore_conflicts', 'update_conflicts'):
                with self.assertRaises(AppendOnlyEventError):
                    ResponseInputObservation.objects.bulk_create([row], **{option: True})
            row.content_hash_after = 'raw text'
            with self.assertRaises(ValidationError):
                ResponseInputObservation.objects.bulk_create([row])
            row.content_hash_after = 'b' * 64
            row.source_event_id = uuid.uuid4()
            with self.assertRaises(ValidationError):
                ResponseInputObservation.objects.bulk_create([row])
            row.source_event_id = None
            row.attempt_id = None
            with self.assertRaises(ValidationError):
                ResponseInputObservation.objects.bulk_create([row])
            row.attempt = self.attempt
            ResponseInputObservation.objects.bulk_create([row])
            with self.assertRaises(AppendOnlyEventError):
                ResponseInputObservation.objects.bulk_create([row])


@skipUnless(connection.vendor == 'postgresql', 'Two committed connections require PostgreSQL')
class ConcurrentInputObservationTests(InputObservationFixture, TransactionTestCase):
    def test_committed_competitor_between_prefetch_and_insert(self):
        # The other connection inserts a legacy row after the lookup has missed
        # it. This exercises the unique index and real savepoint recovery.
        def compete(values):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '3s'")
                return record_stable_input_observation(**values).pk
            finally:
                connections.close_all()

        for conflict in (False, True):
            with self.subTest(conflict=conflict):
                self.stream = uuid.uuid4()
                events = [self.edit(1, legacy=True), self.edit(2)]
                values = self.observation_values(1)
                if conflict:
                    values['content_hash_after'] = 'c' * 64
                committed = []

                def race(execute, sql, params, many, context):
                    if not committed and sql.startswith('INSERT INTO "assessment_responseinputobservation"'):
                        with ThreadPoolExecutor(max_workers=1) as worker:
                            committed.append(worker.submit(compete, values).result(timeout=8))
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(race):
                    if conflict:
                        with self.assertRaisesMessage(ProcessRequestError, 'invalid_event'):
                            self.ingest(events)
                    else:
                        self.assertEqual(len(self.ingest(events)), 2)
                self.assertEqual(len(committed), 1)
                self.assertIsNone(ResponseInputObservation.objects.get(pk=committed[0]).source_event_id)
                self.assertEqual(AssessmentEvent.objects.filter(client_instance_id=self.stream).count(),
                                 0 if conflict else 2)
                self.assertEqual(ResponseInputObservation.objects.filter(client_instance_id=self.stream).count(),
                                 1 if conflict else 2)
