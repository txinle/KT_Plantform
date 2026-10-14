"""A close receipt confirms evidence and lease release in one durable commit."""
import copy
import uuid
from datetime import timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import DatabaseError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.api.test_support import domain_payload
from . import test_capture_bundle as fixtures
from .models import AssessmentEvent, Attempt, CaptureMutationReceipt, ClientCaptureCheckpoint, ExperimentSession
from .writer_lease_services import acquire_attempt_writer_lease


@override_settings(CLOSE_BUNDLE_ENABLED=True, CAPTURE_BUNDLE_ENABLED=True)
class CloseCaptureBundleTests(TestCase):
    setUp = fixtures.CaptureBundleTests.setUp
    transition = fixtures.CaptureBundleTests.transition
    bundle = fixtures.CaptureBundleTests.bundle
    capture = fixtures.CaptureBundleTests.capture
    post = fixtures.CaptureBundleTests.post
    assert_receipt = fixtures.CaptureBundleTests.assert_receipt

    def close_document(self):
        attempt, bundle = self.capture()
        # Each defect subtest starts an independent lease operation on the fixture.
        Attempt.objects.filter(pk=attempt.pk).update(writer_lease_token=None,
            writer_client_instance_id=None, writer_lease_expires_at=None)
        lease = acquire_attempt_writer_lease(attempt, client_instance_id=self.stream)
        leave = copy.deepcopy(bundle['events'][0])
        leave.update(event_id=str(uuid.uuid4()), event_type='item_left', event_version=1,
                     client_sequence_no=2, client_monotonic_ms=200, payload={'leave_reason': 'submit'})
        bundle['events'].append(leave)
        bundle['event_boundary'] = 2
        bundle['checkpoints'][0].update(highest_emitted_sequence_no=2, pending_event_count=2)
        return attempt, {'lease_token': str(lease.token), 'client_instance_id': str(self.stream),
            'capture_operation_id': str(uuid.uuid4()), 'leave_event_id': leave['event_id'], 'capture_bundle': bundle}

    def test_close_commits_evidence_releases_lease_and_replays_original_head(self):
        attempt, document = self.close_document()
        original = self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        self.assertTrue(original['writer_lease_released'])
        self.assertEqual(original['capture_operation_id'], document['capture_operation_id'])
        self.assertEqual(original['response_version'], 0)
        self.assertIsNone(original['active_response_event_id'])
        attempt.refresh_from_db()
        self.assertIsNone(attempt.writer_lease_token)
        replacement = acquire_attempt_writer_lease(attempt, client_instance_id=uuid.uuid4())
        Attempt.objects.filter(pk=attempt.pk).update(response_version=8)
        replay = self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        self.assertEqual(replay, original)
        attempt.refresh_from_db()
        self.assertEqual(attempt.writer_lease_token, replacement.token)
        self.assertEqual(CaptureMutationReceipt.objects.filter(attempt=attempt).count(), 1)

    def test_changed_close_material_conflicts_without_writing(self):
        attempt, document = self.close_document()
        self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        altered = copy.deepcopy(document)
        altered['capture_bundle']['events'][0]['quality_flags'] = ['client_clock_untrusted']
        response = self.post('assessment_process:writer_lease_release', altered, attempt)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(AssessmentEvent.objects.filter(client_instance_id=self.stream).count(), 2)

    def test_bad_checkpoint_wrong_lease_or_missing_leave_roll_back_all(self):
        for defect in ('checkpoint', 'lease', 'leave', 'client', 'event_type'):
            with self.subTest(defect=defect):
                attempt, document = self.close_document()
                if defect == 'checkpoint':
                    document['capture_bundle']['checkpoints'][0]['trigger'] = 'invalid'
                elif defect == 'lease':
                    document['lease_token'] = str(uuid.uuid4())
                elif defect == 'leave':
                    document['leave_event_id'] = str(uuid.uuid4())
                elif defect == 'client':
                    document['client_instance_id'] = str(uuid.uuid4())
                else:
                    document['leave_event_id'] = document['capture_bundle']['events'][0]['event_id']
                response = self.post('assessment_process:writer_lease_release', document, attempt)
                self.assertGreaterEqual(response.status_code, 400, response.content)
                self.assertFalse(AssessmentEvent.objects.filter(client_instance_id=self.stream).exists())
                self.assertFalse(ClientCaptureCheckpoint.objects.filter(client_instance_id=self.stream).exists())
                attempt.refresh_from_db()
                self.assertIsNotNone(attempt.writer_lease_token)

    def terminal_document(self, seconds=1):
        attempt, document = self.close_document()
        now = timezone.now() - timedelta(seconds=seconds)
        Attempt.objects.filter(pk=attempt.pk).update(status=Attempt.Status.SUBMITTED, submitted_at=now)
        ExperimentSession.objects.filter(pk=self.session.pk).update(status=ExperimentSession.Status.COMPLETED,
            completed_at=now, terminal_at=now)
        document['capture_bundle']['events'] = document['capture_bundle']['events'][1:]
        return attempt, document

    def test_finished_last_item_close_accepts_only_recording_facts_within_grace(self):
        attempt, document = self.terminal_document()
        receipt = self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        self.assertEqual(receipt['status'], Attempt.Status.SUBMITTED)
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 0)
        self.assertIsNone(attempt.active_response_event_id)
        self.assertIsNone(attempt.final_response_event_id)

    def test_finished_close_outside_grace_or_with_late_response_is_rejected(self):
        for defect in ('late', 'response'):
            with self.subTest(defect=defect):
                attempt, document = self.terminal_document(seconds=301 if defect == 'late' else 1)
                if defect == 'response':
                    document['capture_bundle']['events'].insert(0, self.transition(attempt, None, 'A'))
                response = self.post('assessment_process:writer_lease_release', document, attempt)
                self.assertEqual(response.status_code, 409, response.content)
                self.assertFalse(AssessmentEvent.objects.filter(client_instance_id=self.stream).exists())
                attempt.refresh_from_db()
                self.assertIsNotNone(attempt.writer_lease_token)

    def test_bare_release_stays_compatible_and_disabled_capability_allows_receipt_recovery(self):
        attempt, document = self.close_document()
        original = self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        with override_settings(CAPTURE_BUNDLE_ENABLED=False):
            from .capture_bundle_services import close_bundle_capability
            self.assertFalse(close_bundle_capability(self.session)['enabled'])
            self.assertEqual(self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle']), original)
            new = copy.deepcopy(document)
            new['capture_operation_id'] = str(uuid.uuid4())
            self.assertEqual(self.post('assessment_process:writer_lease_release', new, attempt).status_code, 409)
        bare = {key: document[key] for key in ('lease_token', 'client_instance_id')}
        self.assertEqual(domain_payload(self.post('assessment_process:writer_lease_release', bare, attempt)), {'success': True})

    def test_runtime_capability_is_gated_independently(self):
        attempt, _ = self.capture()
        url = reverse('student-runtime-question', args=[self.session.pk, attempt.assignment_id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(domain_payload(response)['process']['close_bundle'], {'enabled': True, 'version': 1,
            'max_events': 100, 'max_checkpoints': 100, 'max_bytes': 8388608})
        with override_settings(CLOSE_BUNDLE_ENABLED=False):
            process = domain_payload(self.client.get(url))['process']
            self.assertFalse(process['close_bundle']['enabled'])
            self.assertTrue(process['capture_bundle']['enabled'])

    @override_settings(ATOMIC_SUBMISSION_ENABLED=True, ATOMIC_SUBMISSION_USER_IDS=frozenset({'*'}))
    def test_actual_last_submission_closes_with_identical_final_head(self):
        from .atomic_submission_services import submit_attempt_bundle
        attempt, document = self.close_document()
        self.session.attempts.exclude(pk=attempt.pk).update(status=Attempt.Status.UNANSWERED)
        submit = {'submission_token': str(uuid.uuid4()), 'target_event_id': document['capture_bundle']['events'][0]['event_id'],
            'confidence': None, 'writer_lease_token': document['lease_token'],
            'writer_client_instance_id': document['client_instance_id'],
            'atomic_submission': {**document['capture_bundle'], 'expected_version': 0}}
        # Leave belongs to close, rather than being preconfirmed by submit.
        submit['atomic_submission']['events'] = submit['atomic_submission']['events'][:1]
        submit['atomic_submission']['checkpoints'] = []
        submitted, original = submit_attempt_bundle(student=self.student, attempt_id=attempt.pk, document=submit)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ExperimentSession.Status.COMPLETED)
        document['capture_bundle']['events'] = document['capture_bundle']['events'][1:]
        receipt = self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        self.assertEqual(receipt['final_response_event_id'], original['final_response_event_id'])
        self.assertEqual(receipt['active_response_event_id'], original['final_response_event_id'])
        self.assertEqual(receipt['response_version'], original['response_version'])
        submitted.refresh_from_db()
        self.assertEqual(submitted.final_answer, 'A')

    def test_deadline_close_records_only_facts_inside_frozen_grace(self):
        attempt, document = self.close_document()
        document['capture_bundle']['events'] = document['capture_bundle']['events'][1:]
        self.session.deadline_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=['deadline_at'])
        receipt = self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        self.assertEqual(receipt['response_version'], 0)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ExperimentSession.Status.IN_PROGRESS)

    @override_settings(ATOMIC_SUBMISSION_ENABLED=True, ATOMIC_SUBMISSION_USER_IDS=frozenset({'*'}))
    def test_final_ack_checkpoint_after_last_submit_close_obeys_same_grace(self):
        from .atomic_submission_services import submit_attempt_bundle
        attempt, document = self.close_document()
        self.session.attempts.exclude(pk=attempt.pk).update(status=Attempt.Status.UNANSWERED)
        submit = {'submission_token': str(uuid.uuid4()), 'target_event_id': document['capture_bundle']['events'][0]['event_id'],
            'confidence': None, 'writer_lease_token': document['lease_token'],
            'writer_client_instance_id': document['client_instance_id'],
            'atomic_submission': {**document['capture_bundle'], 'expected_version': 0}}
        submit['atomic_submission']['events'] = submit['atomic_submission']['events'][:1]
        submit['atomic_submission']['checkpoints'] = []
        submit_attempt_bundle(student=self.student, attempt_id=attempt.pk, document=submit)
        document['capture_bundle']['events'] = document['capture_bundle']['events'][1:]
        self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        ack = copy.deepcopy(document['capture_bundle'])
        ack['events'] = []
        ack['checkpoints'][0].update(checkpoint_id=str(uuid.uuid4()), trigger='ack_progress',
            highest_acked_sequence_no=2, highest_contiguous_acked_sequence_no=2, pending_event_count=0)
        self.assert_receipt(self.post('assessment_process:capture_v2_batch', {'capture_bundle': ack}), ack)
        old = timezone.now() - timedelta(seconds=301)
        ExperimentSession.objects.filter(pk=self.session.pk).update(terminal_at=old, completed_at=old)
        ack['checkpoints'][0]['checkpoint_id'] = str(uuid.uuid4())
        response = self.post('assessment_process:capture_v2_batch', {'capture_bundle': ack})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(ClientCaptureCheckpoint.objects.filter(pk=ack['checkpoints'][0]['checkpoint_id']).exists())

    def test_terminal_batch_rejects_late_response_and_revoked_consent(self):
        from apps.accounts.models import ResearchConsent
        attempt, document = self.terminal_document()
        bundle = document['capture_bundle']
        bundle['events'].insert(0, self.transition(attempt, None, 'A'))
        response = self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(AssessmentEvent.objects.filter(client_instance_id=self.stream).exists())
        bundle['events'] = []
        ResearchConsent.objects.filter(student=self.student).delete()
        response = self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle})
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(ClientCaptureCheckpoint.objects.filter(client_instance_id=self.stream).exists())

    def test_close_uses_the_session_frozen_grace_parameter(self):
        from .recording_capture_services import CAPTURE_CONTRACT_V1
        attempt, document = self.terminal_document(seconds=3)
        with patch.dict(CAPTURE_CONTRACT_V1['parameters'], terminal_grace_seconds=2):
            self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])

    def test_committed_close_receipt_remains_subject_to_course_and_consent(self):
        from apps.accounts.models import Course, CourseEnrollment, ClassGroup, ResearchConsent
        attempt, document = self.close_document()
        course = Course.objects.create(owner=self.teacher, code='CLOSE-RECEIPT', term='2026', name='Capture')
        group = ClassGroup.objects.create(owner=self.teacher, course=course, name='Capture class')
        enrollment = CourseEnrollment.objects.create(course=course, class_group=group, student=self.student)
        experiment = self.version.experiment
        experiment.course = course
        experiment.save(update_fields=['course'])
        self.assert_receipt(self.post('assessment_process:writer_lease_release', document, attempt), document['capture_bundle'])
        CourseEnrollment.objects.filter(pk=enrollment.pk).update(is_active=False)
        response = self.post('assessment_process:writer_lease_release', document, attempt)
        self.assertEqual(response.status_code, 409, response.content)
        CourseEnrollment.objects.filter(pk=enrollment.pk).update(is_active=True)
        ResearchConsent.objects.filter(student=self.student).delete()
        response = self.post('assessment_process:writer_lease_release', document, attempt)
        self.assertEqual(response.status_code, 403, response.content)


@override_settings(CLOSE_BUNDLE_ENABLED=True, CAPTURE_BUNDLE_ENABLED=True)
class CloseCaptureCommitTests(TransactionTestCase):
    setUp = fixtures.CaptureBundleTests.setUp
    transition = fixtures.CaptureBundleTests.transition
    bundle = fixtures.CaptureBundleTests.bundle
    capture = fixtures.CaptureBundleTests.capture
    close_document = CloseCaptureBundleTests.close_document

    def test_failed_commit_rolls_back_close_evidence_receipt_and_lease(self):
        from .capture_bundle_services import close_attempt_capture_bundle
        attempt, document = self.close_document()
        with patch.object(connection, 'commit', side_effect=DatabaseError('injected commit failure')):
            with self.assertRaises(DatabaseError):
                close_attempt_capture_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertFalse(CaptureMutationReceipt.objects.exists())
        self.assertFalse(AssessmentEvent.objects.filter(client_instance_id=self.stream).exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())
        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.writer_lease_token)

    def test_response_loss_recovers_original_close_after_lease_and_grace_expiry(self):
        from .capture_bundle_services import close_attempt_capture_bundle
        attempt, document = self.close_document()
        with patch.object(connection, 'run_and_clear_commit_hooks', side_effect=RuntimeError('lost response')):
            with self.assertRaises(RuntimeError):
                close_attempt_capture_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        original = CaptureMutationReceipt.objects.get(attempt=attempt).receipt
        old = timezone.now() - timedelta(seconds=301)
        Attempt.objects.filter(pk=attempt.pk).update(status=Attempt.Status.SUBMITTED, submitted_at=old)
        ExperimentSession.objects.filter(pk=self.session.pk).update(status=ExperimentSession.Status.COMPLETED, terminal_at=old)
        with CaptureQueriesContext(connection) as queries:
            replay = close_attempt_capture_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertEqual(replay, original)
        self.assertFalse([row for row in queries if row['sql'].startswith(('INSERT', 'UPDATE', 'DELETE'))])

    def test_concurrent_identical_close_returns_one_original_receipt(self):
        from .capture_bundle_services import close_attempt_capture_bundle
        attempt, document = self.close_document()
        barrier = Barrier(2)
        def close():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return close_attempt_capture_bundle(student=self.student, attempt_id=attempt.pk, document=copy.deepcopy(document))
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = [future.result(timeout=30) for future in (executor.submit(close), executor.submit(close))]
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(CaptureMutationReceipt.objects.filter(attempt=attempt).count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(client_instance_id=self.stream).count(), 2)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.writer_lease_token)
