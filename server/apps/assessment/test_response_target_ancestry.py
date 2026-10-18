"""A deep response history must not require one SQL round trip per ancestor."""
import uuid

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.question_bank.models import Question
from .capture_v2_services import ResponseVersionConflict, select_response_target
from .models import AssessmentEvent, _assessment_event_append_scope
from . import test_capture_v2 as capture_fixtures


class ResponseTargetAncestryTests(TestCase):
    setUp = capture_fixtures.CaptureV2Tests.setUp
    transition = capture_fixtures.CaptureV2Tests.transition
    ingest = capture_fixtures.CaptureV2Tests.ingest

    def chain(self, count):
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SHORT]
        before, parent, events = '', None, []
        for sequence in range(1, count + 1):
            after = 'A' if sequence % 2 else 'B'
            event = self.transition(attempt, before, after, parent=parent, sequence=sequence)
            events.append(event)
            before, parent = after, event['event_id']
        for offset in range(0, count, 100):
            self.ingest(events[offset:offset + 100])
        return attempt, events

    def test_chain_lengths_1_100_1000_use_at_most_two_queries(self):
        attempt, events = self.chain(1000)
        attempt.active_response_event_id = uuid.UUID(events[0]['event_id'])
        for depth in (1, 100, 1000):
            with self.subTest(depth=depth), CaptureQueriesContext(connection) as queries:
                selected = select_response_target(attempt, events[depth - 1]['event_id'], attempt.response_version)
            self.assertEqual(str(selected.pk), events[depth - 1]['event_id'])
            self.assertLessEqual(len(queries), 2, f'{depth} ancestors required {len(queries)} SQL queries')

    def test_unset_active_pointer_needs_only_the_target_lookup(self):
        attempt, events = self.chain(100)
        with CaptureQueriesContext(connection) as queries:
            select_response_target(attempt, events[-1]['event_id'], attempt.response_version)
        self.assertEqual(len(queries), 1)

    def test_other_branch_and_stale_version_keep_conflict_semantics(self):
        attempt, events = self.chain(3)
        alternate = self.transition(attempt, '', 'C', sequence=4)
        self.ingest([alternate])
        attempt.active_response_event_id = uuid.UUID(events[1]['event_id'])
        with self.assertRaises(ResponseVersionConflict):
            select_response_target(attempt, alternate['event_id'], attempt.response_version)
        with self.assertRaises(ResponseVersionConflict):
            select_response_target(attempt, events[-1]['event_id'], attempt.response_version + 1)

    def append_fixture(self, attempt, *, event_type='response_transition', parent=None, event_id=None):
        # Isolate invalid historical links to test traversal scoping and cycle safety.
        number = AssessmentEvent.objects.filter(session=self.session).count() + 1
        row = AssessmentEvent(event_id=event_id or uuid.uuid4(), session=self.session, student=self.student,
            attempt=attempt, assignment=attempt.assignment, question_snapshot=attempt.assignment.snapshot,
            event_domain='assessment', event_type=event_type, actor_source='client',
            server_ingest_sequence_no=10000 + number,
            payload={'parent_event_id': str(parent) if parent else None,
                'after_state': {'question_type': 'short_answer', 'value': 'fixture'}})
        with _assessment_event_append_scope():
            AssessmentEvent.objects.bulk_create([row])
        return row

    def test_ancestor_walk_does_not_cross_attempts_or_non_response_events(self):
        attempt, events = self.chain(1)
        attempt.active_response_event_id = uuid.UUID(events[0]['event_id'])
        for foreign_attempt, event_type in (
            (self.attempts[Question.Type.FILL_BLANK], 'response_transition'),
            (attempt, 'item_left'),
        ):
            with self.subTest(event_type=event_type):
                bridge = self.append_fixture(foreign_attempt, event_type=event_type,
                    parent=attempt.active_response_event_id)
                target = self.append_fixture(attempt, parent=bridge.pk)
                with self.assertRaises(ResponseVersionConflict):
                    select_response_target(attempt, target.pk, attempt.response_version)

    def test_cycle_terminates_as_conflict_in_one_ancestor_query(self):
        attempt, events = self.chain(1)
        attempt.active_response_event_id = uuid.UUID(events[0]['event_id'])
        cycle_id = uuid.uuid4()
        target = self.append_fixture(attempt, parent=cycle_id, event_id=cycle_id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '1000ms'")
        with CaptureQueriesContext(connection) as queries, self.assertRaises(ResponseVersionConflict):
            select_response_target(attempt, target.pk, attempt.response_version)
        self.assertLessEqual(len(queries), 2)
