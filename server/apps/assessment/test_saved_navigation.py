"""Ordinary navigation must preserve a draft and real process evidence together."""
from apps.api.test_support import domain_payload
from copy import deepcopy
import json
import uuid
from datetime import timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import DatabaseError, close_old_connections, connection
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.question_bank.models import Question
from . import test_capture_v2 as capture_fixtures
from . import test_atomic_submission as atomic_fixtures
from .models import AssessmentEvent, Attempt, ClientCaptureCheckpoint, NavigationReceipt, SubmissionEvent


@override_settings(SAVE_AND_LEAVE_ENABLED=True, SAVE_AND_LEAVE_USER_IDS=frozenset({'*'}))
class SavedNavigationTests(TestCase):
    setUp = capture_fixtures.CaptureV2Tests.setUp
    transition = capture_fixtures.CaptureV2Tests.transition

    def bundle(self):
        attempt, submission = atomic_fixtures.AtomicSubmissionTests.bundle(self)
        bundle = submission['atomic_submission']
        destination = self.attempts[Question.Type.SHORT].assignment_id
        left = deepcopy(bundle['events'][0])
        left.update(event_id=str(uuid.uuid4()), event_type='item_left', event_version=2, client_sequence_no=2,
                    client_monotonic_ms=200,
                    payload={'leave_reason': 'navigation', 'to_assignment_id': destination})
        bundle['events'].append(left)
        bundle['event_boundary'] = 2
        bundle['checkpoints'][0].update(highest_emitted_sequence_no=2, pending_event_count=2,
                                         client_monotonic_ms=200, trigger='navigation')
        navigation_token = str(uuid.uuid4())
        left['correlation_id'] = navigation_token
        return attempt, {'navigation_token': navigation_token, 'leave_event_id': left['event_id'],
                         'target_event_id': submission['target_event_id'],
                         'to_assignment_id': destination,
                         'client_clicked_at': timezone.now().isoformat(),
                         'save_and_leave': bundle}

    def post(self, attempt, document):
        return self.client.post(reverse('assessment_api:save_draft', args=[attempt.pk]),
                                data=json.dumps(document), content_type='application/json')

    def test_saves_and_leaves_in_one_confirmed_request_without_formal_submission(self):
        attempt, document = self.bundle()
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['navigation_token'], document['navigation_token'])
        self.assertEqual(len(receipt['acknowledgements']), 2)
        self.assertEqual(receipt['checkpoint_ids'], [document['save_and_leave']['checkpoints'][0]['checkpoint_id']])
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.DRAFT)
        self.assertEqual(attempt.draft_answer, 'A')
        self.assertEqual(attempt.response_version, 1)
        self.assertIsNone(attempt.final_response_event_id)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertEqual(AssessmentEvent.objects.filter(event_type='item_left').count(), 1)
        self.assertFalse(AssessmentEvent.objects.filter(attempt__assignment_id=document['to_assignment_id'],
                                                       event_type='item_entered').exists())
        self.assertIsNone(attempt.writer_lease_token)

    def test_lost_response_replays_original_receipt_after_later_draft_changes(self):
        attempt, document = self.bundle()
        first = self.post(attempt, document)
        self.assertEqual(first.status_code, 200, first.content)
        Attempt.objects.filter(pk=attempt.pk).update(response_version=5)
        replay = self.post(attempt, document)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(domain_payload(replay), domain_payload(first))
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 5)
        self.assertEqual(AssessmentEvent.objects.filter(event_type='item_left').count(), 1)

    def test_same_token_rejects_changed_destination(self):
        attempt, document = self.bundle()
        self.assertEqual(self.post(attempt, document).status_code, 200)
        changed = deepcopy(document)
        changed['to_assignment_id'] = self.attempts[Question.Type.MULTIPLE].assignment_id
        self.assertEqual(self.post(attempt, changed).status_code, 409)

    def test_bad_checkpoint_rolls_back_events_draft_and_lease(self):
        attempt, document = self.bundle()
        document['save_and_leave']['checkpoints'][0]['trigger'] = 'invalid'
        self.assertEqual(self.post(attempt, document).status_code, 400)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.active_response_event_id)
        self.assertIsNone(attempt.writer_lease_token)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_transition').exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_navigation_without_answer_never_fabricates_a_draft(self):
        attempt, document = self.bundle()
        document['target_event_id'] = None
        document['save_and_leave']['events'] = [document['save_and_leave']['events'][1]]
        document['save_and_leave']['events'][0]['client_sequence_no'] = 1
        document['save_and_leave']['event_boundary'] = 1
        document['save_and_leave']['checkpoints'][0].update(highest_emitted_sequence_no=1, pending_event_count=1)
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.draft_answer)
        self.assertIsNone(attempt.draft_saved_at)
        self.assertEqual(attempt.response_version, 0)

    @override_settings(SAVE_AND_LEAVE_ENABLED=False)
    def test_disabled_protocol_does_not_write_any_envelope_facts(self):
        attempt, document = self.bundle()
        self.assertEqual(self.post(attempt, document).status_code, 409)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='item_left').exists())

    def test_version_conflict_is_confirmed_rejection_and_rolls_back_all_facts(self):
        attempt, document = self.bundle()
        Attempt.objects.filter(pk=attempt.pk).update(response_version=3)
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(domain_payload(response)['error']['code'], 'RESPONSE_VERSION_CONFLICT')
        self.assertEqual(domain_payload(response)['error']['fields']['navigation_token'], document['navigation_token'])
        self.assertTrue(domain_payload(response)['error']['fields']['navigation_rejected'])
        self.assertFalse(AssessmentEvent.objects.filter(event_type='item_left').exists())
        self.assertFalse(NavigationReceipt.objects.exists())
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 3)
        self.assertIsNone(attempt.writer_lease_token)

    def test_other_client_lease_rejects_without_stealing_or_writing(self):
        from .writer_lease_services import acquire_attempt_writer_lease
        attempt, document = self.bundle()
        lease = acquire_attempt_writer_lease(attempt, client_instance_id=uuid.uuid4())
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(domain_payload(response)['error']['code'], 'writer_lease_conflict')
        self.assertTrue(domain_payload(response)['error']['fields']['navigation_rejected'])
        attempt.refresh_from_db()
        self.assertEqual(attempt.writer_lease_token, lease.token)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='item_left').exists())

    def test_unanswered_navigation_also_requires_the_frozen_response_version(self):
        attempt, document = self.bundle()
        document['target_event_id'] = None
        document['save_and_leave']['events'] = [document['save_and_leave']['events'][1]]
        document['save_and_leave']['events'][0]['client_sequence_no'] = 1
        document['save_and_leave']['event_boundary'] = 1
        document['save_and_leave']['checkpoints'][0].update(highest_emitted_sequence_no=1, pending_event_count=1)
        Attempt.objects.filter(pk=attempt.pk).update(response_version=3)
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(NavigationReceipt.objects.exists())

    def test_receipt_replay_survives_disabled_protocol(self):
        attempt, document = self.bundle()
        first = self.post(attempt, document)
        self.assertEqual(first.status_code, 200)
        with override_settings(SAVE_AND_LEAVE_ENABLED=False):
            replay = self.post(attempt, document)
        self.assertEqual(domain_payload(replay), domain_payload(first))

    def test_next_question_read_failure_cannot_undo_the_saved_draft(self):
        attempt, document = self.bundle()
        self.assertEqual(self.post(attempt, document).status_code, 200)
        self.assertEqual(self.client.get(reverse('assessment:question', args=[self.session.pk, 999999])).status_code, 404)
        attempt.refresh_from_db()
        self.assertEqual(attempt.draft_answer, 'A')
        self.assertTrue(NavigationReceipt.objects.filter(attempt=attempt).exists())

    def test_material_conflict_never_marks_an_existing_receipt_as_rejected(self):
        attempt, document = self.bundle()
        self.assertEqual(self.post(attempt, document).status_code, 200)
        document['to_assignment_id'] = self.attempts[Question.Type.MULTIPLE].assignment_id
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(domain_payload(response)['error']['fields'].get('navigation_rejected'))

    def test_received_after_deadline_rejects_without_new_evidence(self):
        from .saved_navigation_services import save_and_leave_attempt
        attempt, document = self.bundle()
        self.session.deadline_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=['deadline_at'])
        with self.assertRaises(ValidationError) as outcome:
            save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=document,
                                   request_received_at=timezone.now())
        self.assertTrue(hasattr(outcome.exception, 'navigation_rejection_token'))
        self.assertFalse(NavigationReceipt.objects.exists())
        self.assertFalse(AssessmentEvent.objects.filter(event_type='item_left').exists())


@override_settings(SAVE_AND_LEAVE_ENABLED=True, SAVE_AND_LEAVE_USER_IDS=frozenset({'*'}))
class SavedNavigationCommitTests(TransactionTestCase):
    setUp = capture_fixtures.CaptureV2Tests.setUp
    transition = capture_fixtures.CaptureV2Tests.transition
    bundle = SavedNavigationTests.bundle

    def test_commit_failure_has_no_success_receipt_or_confirmed_rejection(self):
        from .saved_navigation_services import save_and_leave_attempt
        attempt, document = self.bundle()
        with patch.object(connection, 'commit', side_effect=DatabaseError('injected commit failure')):
            with self.assertRaises(DatabaseError) as outcome:
                save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertFalse(hasattr(outcome.exception, 'navigation_rejection_token'))
        self.assertFalse(NavigationReceipt.objects.exists())
        self.assertFalse(AssessmentEvent.objects.filter(event_type='item_left').exists())
        attempt.refresh_from_db()
        self.assertIsNone(attempt.active_response_event_id)

    def test_post_commit_error_is_unknown_and_original_token_recovers_the_committed_receipt(self):
        from .saved_navigation_services import save_and_leave_attempt
        attempt, document = self.bundle()
        with patch.object(connection, 'run_and_clear_commit_hooks', side_effect=ValidationError('injected response failure')):
            with self.assertRaises(ValidationError) as outcome:
                save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertFalse(hasattr(outcome.exception, 'navigation_rejection_token'))
        _, recovered = save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertTrue(recovered['committed'])
        self.assertEqual(NavigationReceipt.objects.filter(attempt=attempt).count(), 1)

    def test_concurrent_original_token_produces_one_receipt_and_one_draft_version(self):
        from .saved_navigation_services import save_and_leave_attempt
        attempt, document = self.bundle()
        barrier = Barrier(2)
        def save():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return save_and_leave_attempt(student=self.student, attempt_id=attempt.pk,
                                              document=deepcopy(document))[1]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            left, right = [future.result(timeout=30) for future in [executor.submit(save), executor.submit(save)]]
        self.assertEqual(left, right)
        self.assertEqual(NavigationReceipt.objects.filter(attempt=attempt).count(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 1)
