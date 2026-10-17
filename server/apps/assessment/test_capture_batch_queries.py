"""Repeated capture references must not add a database round trip per event."""
import uuid
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.question_bank.models import Question
from .models import AssessmentEvent, ResponseSnapshot
from . import test_capture_v2 as capture_fixtures


class CaptureBatchQueryTests(TestCase):
    setUp = capture_fixtures.CaptureV2Tests.setUp
    transition = capture_fixtures.CaptureV2Tests.transition
    ingest = capture_fixtures.CaptureV2Tests.ingest

    def chain(self, count=74):
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SHORT]
        before, parent, events = '', None, []
        for sequence in range(1, count + 1):
            after = 'A' if sequence % 2 else ''
            event = self.transition(attempt, before, after, parent=parent, sequence=sequence)
            events.append(event)
            before, parent = after, event['event_id']
        return attempt, events

    def test_74_transitions_reuse_schema_parent_predecessor_and_snapshots(self):
        attempt, events = self.chain()
        self.ingest(events[:1])  # Register the schema and create both immutable states.
        with CaptureQueriesContext(connection) as queries:
            self.ingest(events[1:])
        selects = [row['sql'] for row in queries if row['sql'].startswith('SELECT')]
        for table, maximum in [('assessment_eventschemaartifact', 1),
                               ('assessment_responsesnapshot', 1),
                               ('assessment_assessmentevent', 4)]:
            selected = [sql for sql in selects if 'FROM "' + table + '"' in sql]
            self.assertLessEqual(len(selected), maximum, (table, len(selected)))
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt).count(), 74)
        self.assertEqual(ResponseSnapshot.objects.filter(attempt=attempt).count(), 2)
        self.assertEqual(self.ingest(events), self.ingest(events))

    def test_historical_replay_uses_the_actual_predecessor_not_batch_iteration(self):
        _, events = self.chain(4)
        self.ingest(events)
        # A replay can begin in the middle of a persisted stream.
        expected = list(AssessmentEvent.objects.filter(event_id__in=[row['event_id'] for row in events[1:]])
                        .order_by('client_sequence_no').values_list('event_id', flat=True))
        replay = self.ingest(events[1:])
        self.assertEqual([row['event_id'] for row in replay], [str(value) for value in expected])

    def test_new_states_and_events_use_bounded_inserts_and_savepoints(self):
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SHORT]
        before, parent, events = '', None, []
        for sequence in range(1, 21):
            after = str(sequence)
            event = self.transition(attempt, before, after, parent=parent, sequence=sequence)
            events.append(event)
            before, parent = after, event['event_id']
        with CaptureQueriesContext(connection) as queries:
            acknowledgements = self.ingest(events)
        for table in ('assessment_assessmentevent', 'assessment_responsesnapshot'):
            inserts = [row['sql'] for row in queries if row['sql'].startswith('INSERT INTO "' + table + '"')]
            self.assertEqual(len(inserts), 1, (table, len(inserts)))
        self.assertLessEqual(sum(row['sql'].startswith('SAVEPOINT') for row in queries), 6)
        rows = list(AssessmentEvent.objects.filter(attempt=attempt).order_by('server_ingest_sequence_no'))
        self.assertEqual([str(row.pk) for row in rows], [row['event_id'] for row in events])
        self.assertEqual(acknowledgements, self.ingest(events))
        self.assertEqual(ResponseSnapshot.objects.filter(attempt=attempt).count(), 21)

    def test_bulk_event_identity_race_replays_exact_material_after_rollback(self):
        from . import event_services
        attempt, events = self.chain(2)
        expected = self.ingest(events)
        self.session.refresh_from_db()
        next_sequence = self.session.next_ingest_sequence_no
        original = event_services._resolve_idempotent_replay
        calls = 0
        def miss_prefetched_identity(**kwargs):
            nonlocal calls
            calls += 1
            return None if calls <= 2 else original(**kwargs)
        with patch.object(event_services, '_resolve_idempotent_replay', side_effect=miss_prefetched_identity):
            self.assertEqual(self.ingest(events), expected)
        self.assertGreater(calls, 2)
        self.session.refresh_from_db()
        self.assertEqual(self.session.next_ingest_sequence_no, next_sequence)
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt).count(), 2)

    def test_bulk_snapshot_race_rebuilds_event_snapshot_references(self):
        from . import event_batch_services
        from .response_snapshot_services import create_response_snapshot
        attempt, events = self.chain(1)
        snapshot = create_response_snapshot(attempt=attempt, content='A')
        original = event_batch_services._capture_batch_context
        calls = 0
        def miss_prefetched_snapshot(*args):
            nonlocal calls
            calls += 1
            context = original(*args)
            if calls == 1:
                context['snapshots'] = {}
            return context
        with patch.object(event_batch_services, '_capture_batch_context', side_effect=miss_prefetched_snapshot):
            self.ingest(events)
        self.assertEqual(calls, 2)
        event = AssessmentEvent.objects.get(pk=events[0]['event_id'])
        self.assertEqual(event.payload['after_snapshot_id'], str(snapshot.pk))
        self.assertEqual(ResponseSnapshot.objects.filter(attempt=attempt).count(), 2)

    def test_snapshot_batch_scope_never_allows_conflict_ignoring_or_updates(self):
        from .models import AppendOnlyEventError, _response_snapshot_append_scope
        with _response_snapshot_append_scope():
            for option in ('ignore_conflicts', 'update_conflicts'):
                with self.subTest(option=option), self.assertRaises(AppendOnlyEventError):
                    ResponseSnapshot.objects.bulk_create([], **{option: True})
        with self.assertRaises(AppendOnlyEventError):
            ResponseSnapshot.objects.bulk_create([])
