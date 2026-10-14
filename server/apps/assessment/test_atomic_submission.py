from apps.api.test_support import domain_payload
import copy
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.question_bank.models import Question
from .models import AssessmentEvent, Attempt, ClientCaptureCheckpoint, SubmissionEvent
from . import test_capture_v2 as capture_fixtures


@override_settings(ATOMIC_SUBMISSION_ENABLED=True, ATOMIC_SUBMISSION_USER_IDS=frozenset({'*'}))
class AtomicSubmissionTests(TestCase):
    setUp = capture_fixtures.CaptureV2Tests.setUp
    transition = capture_fixtures.CaptureV2Tests.transition
    ingest = capture_fixtures.CaptureV2Tests.ingest

    def test_atomic_submission_reuses_one_session_and_attempt_lock(self):
        from django.test.utils import CaptureQueriesContext
        from .atomic_submission_services import submit_attempt_bundle
        attempt, document = self.bundle()
        with CaptureQueriesContext(connection) as queries:
            submitted, _ = submit_attempt_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        for table in ('assessment_experimentsession', 'assessment_attempt'):
            locks = [row['sql'] for row in queries if row['sql'].startswith('SELECT')
                     and f'FROM "{table}"' in row['sql'] and 'FOR UPDATE' in row['sql']]
            self.assertEqual(len(locks), 1, locks)
        self.assertEqual(submitted.draft_answer, 'A')
        self.assertEqual(submitted.final_answer, 'A')
        self.assertEqual(submitted.response_version, 1)

    def test_reused_attempt_replaces_cached_text_snapshot_and_event(self):
        from .atomic_submission_services import submit_attempt_bundle
        from .services import save_draft
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SHORT]
        first = self.transition(attempt, '', 'old', sequence=1)
        self.ingest([first])
        save_draft(attempt.pk, None, target_event_id=first['event_id'], expected_version=0)
        _, document = self.bundle()
        # bundle() chooses a new client stream, so its first sequence remains valid.
        second = self.transition(attempt, 'old', 'new', parent=first['event_id'])
        document['target_event_id'] = second['event_id']
        document['atomic_submission']['events'] = [second]
        document['atomic_submission']['expected_version'] = 1
        submitted, receipt = submit_attempt_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertEqual(submitted.final_answer, 'new')
        self.assertEqual(submitted.final_response_snapshot.content, 'new')
        self.assertEqual(str(submitted.final_response_event_id), second['event_id'])
        self.assertEqual(receipt['response_version'], 2)

    def test_previous_server_insert_can_omit_new_receipt_columns(self):
        attempt = self.attempts[Question.Type.SINGLE]
        quote = connection.ops.quote_name
        columns = [field.column for field in Attempt._meta.concrete_fields
                   if field.name not in {'submission_fingerprint', 'submission_receipt'}]
        column_sql = ', '.join(map(quote, columns))
        table = quote(Attempt._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT {column_sql} FROM {table} WHERE id=%s', [attempt.pk])
            values = cursor.fetchone()
        Attempt.objects.filter(pk=attempt.pk).delete()
        with connection.cursor() as cursor:
            placeholders = ', '.join(['%s'] * len(columns))
            cursor.execute(f'INSERT INTO {table} ({column_sql}) VALUES ({placeholders})', values)
        restored = Attempt.objects.get(pk=attempt.pk)
        self.assertEqual(restored.submission_fingerprint, '')
        self.assertEqual(restored.submission_receipt, {})

    def bundle(self):
        self.stream = uuid.uuid4()
        attempt = self.attempts[Question.Type.SINGLE]
        event = self.transition(attempt, None, 'A')
        checkpoint = {
            'checkpoint_id': str(uuid.uuid4()), 'session_id': self.session.pk,
            'client_instance_id': str(self.stream), 'highest_emitted_sequence_no': 1,
            'highest_acked_sequence_no': 0, 'highest_contiguous_acked_sequence_no': 0,
            'pending_event_count': 1, 'failed_event_count': 0,
            'missing_local_sequence_ranges': [], 'client_monotonic_ms': 100,
            'client_wall_time_utc': timezone.now().isoformat(),
            'client_time_origin': '2026-09-06T00:00:00Z', 'time_sync_id': None,
            'trigger': 'commit', 'quality_flags': [], 'app_version': 'capture-v2',
        }
        document = {
            'submission_token': str(uuid.uuid4()), 'target_event_id': event['event_id'],
            'confidence': None, 'atomic_submission': {
                'version': 1, 'client_instance_id': str(self.stream),
                'event_boundary': 1, 'events': [event], 'checkpoints': [checkpoint],
                'expected_version': 0,
            },
        }
        return attempt, document

    def post(self, attempt, document):
        return self.client.post(reverse('assessment_api:submit', args=[attempt.pk]),
                                data=json.dumps(document), content_type='application/json')

    def test_commits_raw_evidence_draft_grade_and_receipt_together(self):
        attempt, document = self.bundle()
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['acknowledgements'][0]['event_id'], document['target_event_id'])
        self.assertEqual(receipt['checkpoint_ids'], [document['atomic_submission']['checkpoints'][0]['checkpoint_id']])
        attempt.refresh_from_db()
        self.assertEqual(attempt.final_answer, 'A')
        self.assertEqual(receipt['final_response_event_id'], str(attempt.final_response_event_id))
        self.assertEqual(receipt['response_version'], 1)
        self.assertEqual(SubmissionEvent.objects.filter(attempt=attempt).count(), 1)
        self.assertTrue(attempt.submission_fingerprint)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'in_progress')

    def test_last_atomic_submission_finishes_in_same_commit_with_one_dirty_revision(self):
        from .atomic_submission_services import submit_attempt_bundle
        attempt, document = self.bundle()
        self.session.attempts.exclude(pk=attempt.pk).update(status='unanswered')
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            submitted, _ = submit_attempt_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'completed')
        self.assertEqual(self.session.research_source_revision, 1)
        scheduled = [callback for callback in callbacks if getattr(callback, '_coalesced_research_session_ids', ())]
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(submitted.status, 'submitted')

    def test_bad_checkpoint_rolls_back_new_events_and_pointer(self):
        attempt, document = self.bundle()
        document['atomic_submission']['checkpoints'][0]['trigger'] = 'invalid'
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())
        attempt.refresh_from_db()
        self.assertIsNone(attempt.active_response_event_id)
        self.assertEqual(attempt.response_version, 0)
        self.assertFalse(SubmissionEvent.objects.exists())
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_commit_rejected').count(), 1)

    def test_replay_returns_original_receipt_and_changed_material_is_rejected(self):
        attempt, document = self.bundle()
        first = self.post(attempt, document)
        self.assertEqual(first.status_code, 200, first.content)
        replay = self.post(attempt, document)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(domain_payload(first)['acknowledgements'], domain_payload(replay)['acknowledgements'])
        changed = copy.deepcopy(document)
        changed['atomic_submission']['events'][0]['quality_flags'] = ['client_clock_untrusted']
        response = self.post(attempt, changed)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SubmissionEvent.objects.count(), 1)

    def test_preconfirmed_evidence_survives_invalid_bundle(self):
        attempt, document = self.bundle()
        event = document['atomic_submission']['events'][0]
        self.ingest([event])
        second = self.transition(attempt, 'A', 'B', parent=event['event_id'], sequence=2)
        document['atomic_submission']['events'].append(second)
        document['atomic_submission']['event_boundary'] = 2
        document['target_event_id'] = second['event_id']
        document['atomic_submission']['checkpoints'][0]['trigger'] = 'invalid'
        self.assertEqual(self.post(attempt, document).status_code, 400)
        self.assertEqual(list(AssessmentEvent.objects.filter(event_type='response_transition').values_list('event_id', flat=True)), [uuid.UUID(event['event_id'])])

    def test_cross_attempt_material_is_rejected(self):
        attempt, document = self.bundle()
        document['atomic_submission']['events'][0]['attempt_id'] = self.attempts[Question.Type.SHORT].pk
        self.assertEqual(self.post(attempt, document).status_code, 400)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())

    def test_session_only_event_is_rejected_by_single_attempt_envelope(self):
        attempt, document = self.bundle()
        event = copy.deepcopy(document['atomic_submission']['events'][0])
        event.update(event_id=str(uuid.uuid4()), attempt_id=None,
                     event_type='session_started', payload={}, client_sequence_no=2)
        document['atomic_submission']['events'].append(event)
        document['atomic_submission']['event_boundary'] = 2
        self.assertEqual(self.post(attempt, document).status_code, 400)
        self.assertFalse(AssessmentEvent.objects.filter(actor_source='client').exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_each_checkpoint_preserves_existing_256_kib_body_bound(self):
        attempt, document = self.bundle()
        document['atomic_submission']['checkpoints'][0]['missing_local_sequence_ranges'] = [[1, 1]] * 50000
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 413)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    @override_settings(ATOMIC_SUBMISSION_ENABLED=False)
    def test_disabled_capability_rejects_bundle_without_writes(self):
        attempt, document = self.bundle()
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())

    def test_bounded_event_and_checkpoint_counts(self):
        attempt, document = self.bundle()
        for field in ('events', 'checkpoints'):
            oversized = copy.deepcopy(document)
            oversized['atomic_submission'][field] *= 101
            self.assertEqual(self.post(attempt, oversized).status_code, 413)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())

    def test_total_body_bytes_are_bounded(self):
        attempt, document = self.bundle()
        document['padding'] = 'x' * (8 * 1024 * 1024)
        self.assertEqual(self.post(attempt, document).status_code, 413)
        self.assertFalse(SubmissionEvent.objects.exists())

    def test_empty_process_bundle_can_submit_a_confirmed_target(self):
        from .services import save_draft
        attempt, document = self.bundle()
        event = document['atomic_submission']['events'][0]
        self.ingest([event])
        save_draft(attempt.pk, None, target_event_id=event['event_id'], expected_version=0)
        document['atomic_submission'].update(events=[], checkpoints=[], expected_version=1)
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(domain_payload(response)['acknowledgements'], [])
        self.assertEqual(domain_payload(response)['checkpoint_ids'], [])

    def test_flag_requires_course_or_user_allowlist(self):
        from .atomic_submission_services import atomic_submission_capability
        attempt, _ = self.bundle()
        with override_settings(ATOMIC_SUBMISSION_USER_IDS=frozenset(), ATOMIC_SUBMISSION_COURSE_IDS=frozenset()):
            self.assertFalse(atomic_submission_capability(attempt)['enabled'])
        with override_settings(ATOMIC_SUBMISSION_USER_IDS=frozenset({str(self.student.pk)})):
            self.assertTrue(atomic_submission_capability(attempt)['enabled'])

    def test_unknown_material_fields_are_not_silently_ignored(self):
        attempt, document = self.bundle()
        document['answer'] = 'B'
        self.assertEqual(self.post(attempt, document).status_code, 400)
        self.assertFalse(SubmissionEvent.objects.exists())

    def test_invalid_client_click_time_rolls_back_entire_bundle(self):
        attempt, document = self.bundle()
        document['client_clicked_at'] = {'invalid': True}
        self.assertEqual(self.post(attempt, document).status_code, 400)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_closed_session_rolls_back_bundle_and_retains_rejection_audit(self):
        attempt, document = self.bundle()
        self.session.status = 'completed'
        self.session.save(update_fields=['status'])
        self.assertEqual(self.post(attempt, document).status_code, 400)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_commit_rejected').count(), 1)

    def test_changed_rejection_reason_preserves_first_audit_and_returns_conflict(self):
        attempt, document = self.bundle()
        document['atomic_submission']['checkpoints'][0]['trigger'] = 'invalid'
        self.assertEqual(self.post(attempt, document).status_code, 400)
        document['atomic_submission']['checkpoints'][0]['trigger'] = 'commit'
        with override_settings(ATOMIC_SUBMISSION_ENABLED=False):
            response = self.post(attempt, document)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_commit_rejected').count(), 1)
        self.assertFalse(SubmissionEvent.objects.exists())

    def test_recovery_only_acknowledges_original_submission_token(self):
        attempt, document = self.bundle()
        self.assertEqual(self.post(attempt, document).status_code, 200)
        url = reverse('student-attempt-recovery', args=[attempt.pk])
        recovered = domain_payload(self.client.get(url, {'submission_token': document['submission_token']}))
        self.assertTrue(recovered['submission_matches'])
        self.assertTrue(recovered['result']['committed'])
        other = domain_payload(self.client.get(url, {'submission_token': str(uuid.uuid4())}))
        self.assertFalse(other['submission_matches'])
        self.assertNotIn('committed', other['result'])

    def test_version_conflict_rolls_back_new_evidence(self):
        from .services import save_draft
        attempt, document = self.bundle()
        first = document['atomic_submission']['events'][0]
        self.ingest([first])
        save_draft(attempt.pk, None, target_event_id=first['event_id'], expected_version=0)
        second = self.transition(attempt, 'A', 'B', parent=first['event_id'], sequence=2)
        document['atomic_submission']['events'] = [second]
        document['atomic_submission']['event_boundary'] = 2
        document['target_event_id'] = second['event_id']
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 409)
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 1)
        self.assertEqual(attempt.draft_answer, 'A')
        self.assertFalse(AssessmentEvent.objects.filter(pk=second['event_id']).exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_deadline_and_lease_rejection_preserve_evidence_and_answer(self):
        from .writer_lease_services import acquire_attempt_writer_lease
        attempt, document = self.bundle()
        acquire_attempt_writer_lease(attempt, client_instance_id=uuid.uuid4())
        self.assertEqual(self.post(attempt, document).status_code, 409)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())
        self.session.deadline_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=['deadline_at'])
        document['submission_token'] = str(uuid.uuid4())
        self.assertEqual(self.post(attempt, document).status_code, 409)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(SubmissionEvent.objects.exists())


@override_settings(ATOMIC_SUBMISSION_ENABLED=True, ATOMIC_SUBMISSION_USER_IDS=frozenset({'*'}))
class AtomicSubmissionRequiredToolTests(TestCase):
    transition = capture_fixtures.CaptureV2Tests.transition
    ingest = capture_fixtures.CaptureV2Tests.ingest
    bundle = AtomicSubmissionTests.bundle
    post = AtomicSubmissionTests.post

    def setUp(self):
        self.version_configuration = {'research_tools': {'check_enabled': True, 'doubt_enabled': True, 'rules': [{
            'id': 'required-check-single', 'tool': 'check', 'trigger': 'item_enter', 'question_code': 'Q4',
            'after_seconds': 0, 'max_exposures': 1, 'required': True, 'hint_id': ''}]}}
        capture_fixtures.CaptureV2Tests.setUp(self)

    def prepare_submission(self, *, finished):
        from .research_tool_services import record_tool_action
        from .services import save_draft

        attempt, document = self.bundle()
        tool = dict(student=self.student, attempt_id=attempt.pk, tool='check',
                    data={'rule_id': 'required-check-single'}, initiation_type='task_required')
        record_tool_action(**tool, stage='requested', operation_id=str(uuid.uuid4()), answer_event_id=None)
        parent, before = None, None
        for sequence, after in enumerate(('A', 'B', 'A', None, 'A'), 1):
            event = self.transition(attempt, before, after, parent=parent, sequence=sequence)
            self.ingest([event])
            save_draft(attempt.pk, None, target_event_id=event['event_id'], expected_version=sequence - 1)
            before, parent = after, event['event_id']
        for stage in ('presented', 'started', *(['finished'] if finished else [])):
            record_tool_action(**tool, stage=stage, operation_id=str(uuid.uuid4()), answer_event_id=parent)
        commit = self.transition(attempt, 'A', 'B', sequence=6)
        commit.update(event_type='response_commit_requested', payload={'submission_token': document['submission_token']})
        document['target_event_id'] = parent
        document['atomic_submission'].update(events=[commit], event_boundary=6, expected_version=5)
        checkpoint = document['atomic_submission']['checkpoints'][0]
        checkpoint.update(highest_emitted_sequence_no=6, highest_acked_sequence_no=5,
                          highest_contiguous_acked_sequence_no=5)
        return attempt, document, commit

    def test_confirmed_required_check_survives_same_target_atomic_save_at_version_five(self):
        attempt, document, commit = self.prepare_submission(finished=True)
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 5)
        self.assertEqual(str(attempt.final_response_event_id), document['target_event_id'])
        self.assertTrue(domain_payload(response)['committed'])
        self.assertTrue(AssessmentEvent.objects.filter(pk=commit['event_id']).exists())
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type='research_tool',
            payload__stage='finished', payload__data__rule_id='required-check-single').count(), 1)

    def test_unconfirmed_required_check_rejects_atomic_submit_and_rolls_back_new_bundle(self):
        attempt, document, commit = self.prepare_submission(finished=False)
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['error']['code'], 'VALIDATION_ERROR')
        attempt.refresh_from_db()
        self.assertNotEqual(attempt.status, Attempt.Status.SUBMITTED)
        self.assertEqual(attempt.response_version, 5)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertFalse(AssessmentEvent.objects.filter(pk=commit['event_id']).exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.filter(session=attempt.session).exists())
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type='response_transition').count(), 5)


@override_settings(ATOMIC_SUBMISSION_ENABLED=True, ATOMIC_SUBMISSION_USER_IDS=frozenset({'*'}))
class AtomicSubmissionCommitTests(TransactionTestCase):
    setUp = capture_fixtures.CaptureV2Tests.setUp
    transition = capture_fixtures.CaptureV2Tests.transition
    bundle = AtomicSubmissionTests.bundle
    post = AtomicSubmissionTests.post

    def test_success_is_returned_after_real_postgresql_commit(self):
        attempt, document = self.bundle()
        with patch.object(connection, 'commit', wraps=connection.commit) as commit:
            response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(domain_payload(response)['committed'])
        self.assertGreaterEqual(commit.call_count, 1)
        self.assertFalse(connection.in_atomic_block)
        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_receipt['acknowledgements'], domain_payload(response)['acknowledgements'])

    def test_commit_failure_cannot_return_success_or_keep_partial_writes(self):
        attempt, document = self.bundle()
        with patch.object(connection, 'commit', side_effect=DatabaseError('commit unavailable')), patch('django.core.handlers.exception.log_response'):
            with self.assertRaises(DatabaseError):
                self.post(attempt, document)
        attempt.refresh_from_db()
        self.assertFalse(attempt.submission_fingerprint)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())
        self.assertFalse(SubmissionEvent.objects.exists())

    def test_last_item_commit_failure_rolls_back_submission_and_session_completion(self):
        attempt, document = self.bundle()
        self.session.attempts.exclude(pk=attempt.pk).update(status='unanswered')
        with patch.object(connection, 'commit', side_effect=DatabaseError('commit unavailable')), patch('django.core.handlers.exception.log_response'):
            with self.assertRaises(DatabaseError):
                self.post(attempt, document)
        self.session.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(self.session.status, 'in_progress')
        self.assertEqual(self.session.research_source_revision, 0)
        self.assertEqual(attempt.status, 'unvisited')
        self.assertFalse(AssessmentEvent.objects.filter(event_type='session_completed').exists())
        self.assertFalse(SubmissionEvent.objects.exists())
