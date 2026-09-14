import copy
import json
import uuid

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.question_bank.models import Question
from .models import AssessmentEvent, ResponseSnapshot
from .response_snapshot_services import ResponseState
from . import test_response_snapshots as snapshot_fixtures
from .test_support import accept_research_consent


class CaptureV2Tests(TestCase):
    def setUp(self):
        from .recording_capture_services import ensure_capture_contract_v2
        self.capture_contract = ensure_capture_contract_v2()
        snapshot_fixtures.ResponseSnapshotServiceTests.setUp(self)

    def transition(self, attempt, before, after, *, parent=None, sequence=1, client=None):
        kind = attempt.assignment.snapshot.question_type
        return {
            'event_id': str(uuid.uuid4()), 'event_type': 'response_transition',
            'event_version': 1, 'session_id': self.session.pk, 'attempt_id': attempt.pk,
            'client_sequence_no': sequence, 'client_wall_time_utc': timezone.now().isoformat(),
            'client_monotonic_ms': sequence * 100, 'client_time_origin': '2026-09-06T00:00:00Z',
            'quality_flags': [], 'app_version': 'capture-v2',
            'payload': {
                'capture_version': 'capture-v2', 'operation_id': str(uuid.uuid4()),
                'branch_id': str(client or self.stream), 'parent_event_id': parent,
                'before_state': ResponseState(kind, before).state_json,
                'after_state': ResponseState(kind, after).state_json,
                'before_state_hash': ResponseState(kind, before).state_hash,
                'after_state_hash': ResponseState(kind, after).state_hash,
                'input_type': 'insertText', 'source': 'student',
            },
        }

    def ingest(self, events, client=None):
        from .capture_v2_services import ingest_capture_batch
        return ingest_capture_batch(student=self.student, document={
            'client_instance_id': str(client or self.stream), 'events': events,
        })

    def test_repeated_content_retains_every_transition_and_empty_state(self):
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SHORT]
        before, parent, events = '', None, []
        for seq, after in enumerate(['A', 'AB', 'A', ''], 1):
            event = self.transition(attempt, before, after, parent=parent, sequence=seq)
            events.append(event)
            before, parent = after, event['event_id']
        self.ingest(events)
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_transition').count(), 4)
        self.assertEqual(ResponseSnapshot.objects.filter(attempt=attempt).count(), 3)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.current_response_snapshot_id, 'Raw capture must not move saved pointer')

    def test_input_operation_cannot_reference_another_attempt(self):
        from .event_batch_services import ProcessRequestError
        self.stream = uuid.uuid4()
        parent = self.transition(self.attempts[Question.Type.SHORT], '', 'A')
        self.ingest([parent])
        operation = self.transition(self.attempts[Question.Type.FILL_BLANK], '', 'B', sequence=2)
        operation['event_type'] = 'input_operation'
        operation['payload'] = {'capture_version': 'capture-v2', 'operation_id': str(uuid.uuid4()),
            'branch_id': str(self.stream), 'parent_event_id': parent['event_id'],
            'input_type': 'insertText', 'composition': 'none', 'selection_start': 0, 'selection_end': 0}
        with self.assertRaises(ProcessRequestError):
            self.ingest([operation])

    def test_idempotent_replay_and_conflicting_event_id(self):
        from .event_batch_services import ProcessConflictError
        self.stream = uuid.uuid4()
        event = self.transition(self.attempts[Question.Type.SINGLE], None, 'A')
        first = self.ingest([event])
        self.assertEqual(first, self.ingest([event]))
        changed = copy.deepcopy(event)
        changed['payload']['input_type'] = 'clear'
        with self.assertRaises(ProcessConflictError):
            self.ingest([changed])
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_transition').count(), 1)

    def test_invalid_hash_rolls_back_entire_batch(self):
        from .event_batch_services import ProcessRequestError
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SHORT]
        first = self.transition(attempt, '', 'A')
        bad = self.transition(attempt, 'A', 'B', parent=first['event_id'], sequence=2)
        bad['payload']['after_state_hash'] = '0' * 64
        with self.assertRaises(ProcessRequestError):
            self.ingest([first, bad])
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(ResponseSnapshot.objects.filter(attempt=attempt).exists())

    def test_parallel_branches_survive_late_upload_after_submission(self):
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SINGLE]
        first = self.transition(attempt, None, 'A')
        self.ingest([first])
        branch = uuid.uuid4()
        second = self.transition(attempt, 'A', 'B', parent=first['event_id'], client=branch)
        attempt.status = 'submitted'
        attempt.final_answer = 'A'
        attempt.save(update_fields=['status', 'final_answer'])
        self.ingest([second], client=branch)
        attempt.refresh_from_db()
        self.assertEqual(attempt.final_answer, 'A')
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type='response_transition').count(), 2)

    def test_choice_clear_and_multiple_removal_are_states(self):
        self.stream = uuid.uuid4()
        sequence = 0
        for kind, values in [(Question.Type.SINGLE, [None, 'A', 'B', None]),
                             (Question.Type.TRUE_FALSE, [None, 'true', 'false', None]),
                             (Question.Type.MULTIPLE, [[], ['A'], ['A', 'B'], ['B'], []])]:
            parent = None
            for before, after in zip(values, values[1:]):
                sequence += 1
                event = self.transition(self.attempts[kind], before, after, parent=parent, sequence=sequence)
                self.ingest([event])
                parent = event['event_id']
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_transition').count(), 10)

    def test_save_compare_and_swap_and_submit_fixed_state(self):
        from .services import save_draft, submit_attempt
        from .capture_v2_services import ResponseVersionConflict
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SINGLE]
        first = self.transition(attempt, None, 'A')
        second = self.transition(attempt, 'A', 'B', parent=first['event_id'], sequence=2)
        self.ingest([first, second])
        saved = save_draft(attempt.pk, 'IGNORED', target_event_id=first['event_id'], expected_version=0)
        self.assertEqual(saved.draft_answer, 'A')
        with self.assertRaises(ResponseVersionConflict):
            save_draft(attempt.pk, 'B', target_event_id=second['event_id'], expected_version=0)
        final = submit_attempt(attempt.pk, uuid.uuid4(), target_event_id=first['event_id'])
        self.assertEqual(final.final_answer, 'A')
        self.assertEqual(str(final.final_response_event_id), first['event_id'])
        self.assertEqual(final.formal_capture_boundary['response_version'], 1)

    def test_missing_parent_is_retryable_and_unordered_batch_is_reordered(self):
        from .event_batch_services import ProcessRequestError
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SHORT]
        first = self.transition(attempt, '', 'A')
        second = self.transition(attempt, 'A', 'AB', parent=first['event_id'], sequence=2)
        with self.assertRaises(ProcessRequestError) as error:
            self.ingest([second])
        self.assertEqual(error.exception.status, 409)
        self.ingest([second, first])
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_transition').count(), 2)

    def test_explicit_branch_choice_keeps_both_paths(self):
        from .services import save_draft
        from .capture_v2_services import resolve_response_branch, ResponseVersionConflict
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SINGLE]
        first = self.transition(attempt, None, 'A')
        self.ingest([first])
        save_draft(attempt.pk, None, target_event_id=first['event_id'], expected_version=0)
        other = uuid.uuid4()
        alternate = self.transition(attempt, None, 'B', client=other)
        self.ingest([alternate], client=other)
        with self.assertRaises(ResponseVersionConflict):
            save_draft(attempt.pk, None, target_event_id=alternate['event_id'], expected_version=1)
        chosen = resolve_response_branch(student=self.student, attempt_id=attempt.pk,
            candidate_event_ids=[first['event_id'], alternate['event_id']], selected_event_id=alternate['event_id'],
            expected_version=1, operation_id=str(uuid.uuid4()))
        self.assertEqual(chosen.draft_answer, 'B')
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_transition').count(), 2)
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_branch_resolved').count(), 1)

    def _prepare_draft_version_conflict(self):
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SINGLE]
        current = self.transition(attempt, None, 'A')
        candidate = self.transition(
            attempt, 'A', 'B', parent=current['event_id'], sequence=2,
        )
        self.ingest([current, candidate])
        draft_url = reverse('assessment:save_draft', args=[attempt.pk])
        self.assertEqual(
            self.client.post(
                draft_url,
                data=json.dumps({
                    'answer': 'ignored-by-capture-v2',
                    'target_event_id': current['event_id'],
                    'expected_version': 0,
                }),
                content_type='application/json',
            ).status_code,
            200,
        )
        return attempt, current, candidate, draft_url

    def test_draft_version_conflict_appends_server_observation_without_mutating_pointer(self):
        attempt, current, candidate, draft_url = self._prepare_draft_version_conflict()

        response = self.client.post(
            draft_url,
            data=json.dumps({
                'answer': 'ignored-by-capture-v2',
                'target_event_id': candidate['event_id'],
                'expected_version': 0,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'RESPONSE_VERSION_CONFLICT')
        attempt.refresh_from_db()
        self.assertEqual(str(attempt.active_response_event_id), current['event_id'])
        self.assertEqual(attempt.response_version, 1)
        observation = AssessmentEvent.objects.get(
            attempt=attempt,
            event_type='response_pointer_conflict_observed',
        )
        self.assertEqual(observation.actor_source, 'server')
        self.assertEqual(
            observation.payload,
            {
                'capture_version': 'capture-v2',
                'candidate_event_id': candidate['event_id'],
                'current_event_id': current['event_id'],
                'requested_expected_version': 0,
                'current_response_version': 1,
                'request_client_instance_id': None,
            },
        )
        self.assertEqual(str(observation.correlation_id), candidate['event_id'])
        self.assertIsNotNone(observation.server_received_at)

    def test_repeated_draft_version_conflicts_are_distinct_server_observations(self):
        attempt, _current, candidate, draft_url = self._prepare_draft_version_conflict()
        request = {
            'answer': 'ignored-by-capture-v2',
            'target_event_id': candidate['event_id'],
            'expected_version': 0,
        }

        first = self.client.post(draft_url, data=json.dumps(request), content_type='application/json')
        second = self.client.post(draft_url, data=json.dumps(request), content_type='application/json')

        self.assertEqual((first.status_code, second.status_code), (409, 409))
        observations = list(
            AssessmentEvent.objects.filter(
                attempt=attempt,
                event_type='response_pointer_conflict_observed',
            ).order_by('server_ingest_sequence_no')
        )
        self.assertEqual(len(observations), 2)
        self.assertNotEqual(observations[0].event_id, observations[1].event_id)
        self.assertEqual(
            [str(event.correlation_id) for event in observations],
            [candidate['event_id'], candidate['event_id']],
        )

    def test_unauthorized_draft_post_does_not_append_conflict_observation(self):
        attempt, _current, candidate, draft_url = self._prepare_draft_version_conflict()
        stranger = User.objects.create_user(
            username='capture-v2-conflict-stranger',
            student_number='capture-v2-conflict-stranger',
            role=User.Role.STUDENT,
        )
        accept_research_consent(stranger)
        self.client.force_login(stranger)

        response = self.client.post(
            draft_url,
            data=json.dumps({
                'answer': 'ignored-by-capture-v2',
                'target_event_id': candidate['event_id'],
                'expected_version': 0,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            AssessmentEvent.objects.filter(
                attempt=attempt,
                event_type='response_pointer_conflict_observed',
            ).exists()
        )
