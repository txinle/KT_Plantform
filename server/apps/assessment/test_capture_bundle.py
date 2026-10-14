"""Bounded capture evidence shares the existing mutation's commit boundary."""
import copy
import json
import uuid
from datetime import timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.api.test_support import domain_payload
from apps.question_bank.models import Question
from . import test_atomic_submission as fixtures
from .models import AssessmentEvent, ClientCaptureCheckpoint, ResponseSnapshot


class CaptureBundleTests(TestCase):
    setUp = fixtures.AtomicSubmissionTests.setUp
    transition = fixtures.AtomicSubmissionTests.transition
    bundle = fixtures.AtomicSubmissionTests.bundle

    def capture(self):
        attempt, submission = self.bundle()
        bundle = submission['atomic_submission']
        bundle.pop('expected_version')
        return attempt, bundle

    def test_transport_rollback_keeps_original_receipt_recovery(self):
        from django.test import override_settings
        from .capture_bundle_services import capture_bundle_capability, save_draft_capture_bundle
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        _, original = save_draft_capture_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        with override_settings(CAPTURE_BUNDLE_ENABLED=False):
            capability = capture_bundle_capability(self.session)
            self.assertFalse(capability['enabled'])
            self.assertTrue(capability['recovery_supported'])
            _, recovered = save_draft_capture_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertEqual(recovered, original)

    def post(self, name, document, attempt=None):
        args = [attempt.pk] if attempt else []
        return self.client.post(reverse(name, args=args), data=json.dumps(document),
                                content_type='application/json')

    def assert_receipt(self, response, bundle):
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['success'])
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['checkpoint_ids'], [row['checkpoint_id'] for row in bundle['checkpoints']])
        self.assertEqual([row['event_id'] for row in receipt['acknowledgements']],
                         [row['event_id'] for row in bundle['events']])
        for ack in receipt['acknowledgements']:
            event = AssessmentEvent.objects.get(pk=ack['event_id'])
            self.assertEqual(ack['client_sequence_no'], event.client_sequence_no)
            self.assertEqual(ack['server_ingest_sequence_no'], event.server_ingest_sequence_no)
        return receipt

    def draft_document(self, bundle):
        return {'target_event_id': bundle['events'][0]['event_id'], 'expected_version': 0,
                'capture_bundle': bundle, 'capture_operation_id': str(uuid.uuid4())}

    def tool_document(self, bundle):
        self.version.configuration = {'research_tools': {'doubt_enabled': True}}
        self.version.save(update_fields=['configuration'])
        return {'tool': 'doubt', 'stage': 'mark_added', 'operation_id': str(uuid.uuid4()),
                'answer_event_id': bundle['events'][0]['event_id'], 'data': {},
                'capture_bundle': bundle}

    def test_batch_commits_original_events_checkpoints_and_exact_replay_ack(self):
        _, bundle = self.capture()
        first = self.assert_receipt(self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle}), bundle)
        replay = self.assert_receipt(self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle}), bundle)
        self.assertEqual(first, replay)
        self.assertEqual(ClientCaptureCheckpoint.objects.count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(event_type='response_transition').count(), 1)

    def test_batch_accepts_multiple_attempts_and_session_events_of_one_session(self):
        _, bundle = self.capture()
        second = self.transition(self.attempts[Question.Type.SHORT], '', 'A', sequence=2)
        session_event = copy.deepcopy(second)
        session_event.update(event_id=str(uuid.uuid4()), event_type='session_started',
                             attempt_id=None, client_sequence_no=3, client_monotonic_ms=300, payload={})
        bundle.update(events=[*bundle['events'], second, session_event], event_boundary=3)
        self.assert_receipt(self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle}), bundle)

    def test_draft_commits_capture_and_saved_pointer_together(self):
        attempt, bundle = self.capture()
        receipt = self.assert_receipt(self.post('assessment_api:save_draft', self.draft_document(bundle), attempt), bundle)
        attempt.refresh_from_db()
        self.assertEqual(attempt.draft_answer, 'A')
        self.assertEqual(receipt['response_version'], 1)
        self.assertEqual(str(attempt.active_response_event_id), bundle['events'][0]['event_id'])

    def test_tool_commits_capture_with_actual_authorized_action(self):
        attempt, bundle = self.capture()
        document = self.tool_document(bundle)
        self.assert_receipt(self.post('assessment_process:research_tools', document, attempt), bundle)
        action = AssessmentEvent.objects.get(pk=document['operation_id'])
        self.assertEqual(action.payload['answer_event_id'], bundle['events'][0]['event_id'])

    def test_invalid_checkpoint_rolls_back_batch_draft_and_tool(self):
        for endpoint in ('batch', 'draft', 'tool'):
            with self.subTest(endpoint=endpoint):
                attempt, bundle = self.capture()
                bundle['checkpoints'][0]['trigger'] = 'invalid'
                if endpoint == 'batch':
                    response = self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle})
                elif endpoint == 'draft':
                    response = self.post('assessment_api:save_draft', self.draft_document(bundle), attempt)
                else:
                    response = self.post('assessment_process:research_tools', self.tool_document(bundle), attempt)
                self.assertGreaterEqual(response.status_code, 400)
                self.assertFalse(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).exists())
                self.assertFalse(ResponseSnapshot.objects.filter(attempt=attempt).exists())
                self.assertFalse(ClientCaptureCheckpoint.objects.exists())
                attempt.refresh_from_db()
                self.assertEqual(attempt.response_version, 0)

    def test_business_rejection_rolls_back_capture(self):
        attempt, bundle = self.capture()
        document = self.tool_document(bundle)
        document['stage'] = 'invalid'
        response = self.post('assessment_process:research_tools', document, attempt)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_draft_conflicting_version_rolls_back_capture(self):
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        document['expected_version'] = 99
        response = self.post('assessment_api:save_draft', document, attempt)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())
        self.assertFalse(AssessmentEvent.objects.filter(event_type='response_pointer_conflict_observed').exists())

    def test_unknown_fields_boundary_and_checkpoint_links_reject_without_writes(self):
        for material in ('unknown', 'boundary', 'checkpoint_client', 'checkpoint_session', 'event_session'):
            with self.subTest(material=material):
                _, bundle = self.capture()
                if material == 'unknown':
                    bundle['ignored'] = True
                elif material == 'boundary':
                    bundle['event_boundary'] = 0
                elif material == 'checkpoint_client':
                    bundle['checkpoints'][0]['client_instance_id'] = str(uuid.uuid4())
                elif material == 'checkpoint_session':
                    bundle['checkpoints'][0]['session_id'] += 99999
                else:
                    bundle['events'][0]['session_id'] += 99999
                response = self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle})
                self.assertGreaterEqual(response.status_code, 400)
                self.assertFalse(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).exists())
                self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_attempt_mutations_reject_other_attempt_capture(self):
        for endpoint in ('draft', 'tool'):
            with self.subTest(endpoint=endpoint):
                attempt, bundle = self.capture()
                other = self.transition(self.attempts[Question.Type.SHORT], '', 'A', sequence=2)
                bundle.update(events=[*bundle['events'], other], event_boundary=2)
                document = self.draft_document(bundle) if endpoint == 'draft' else self.tool_document(bundle)
                name = 'assessment_api:save_draft' if endpoint == 'draft' else 'assessment_process:research_tools'
                response = self.post(name, document, attempt)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertFalse(AssessmentEvent.objects.filter(pk=other['event_id']).exists())

    def test_checkpoint_material_replay_conflict_rolls_back_new_event(self):
        attempt, bundle = self.capture()
        self.assert_receipt(self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle}), bundle)
        changed = copy.deepcopy(bundle)
        event = self.transition(attempt, 'A', 'B', parent=bundle['events'][0]['event_id'], sequence=2)
        changed.update(events=[event], event_boundary=2)
        changed['checkpoints'][0]['pending_event_count'] = 2
        response = self.post('assessment_process:capture_v2_batch', {'capture_bundle': changed})
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(AssessmentEvent.objects.filter(pk=event['event_id']).exists())

    def test_batch_limits_stay_bounded(self):
        for field in ('events', 'checkpoints'):
            with self.subTest(field=field):
                _, bundle = self.capture()
                bundle[field] *= 101
                response = self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle})
                self.assertEqual(response.status_code, 413, response.content)
                self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_legacy_flat_batch_receipt_is_preserved(self):
        _, bundle = self.capture()
        response = self.post('assessment_process:capture_v2_batch', {
            'client_instance_id': bundle['client_instance_id'], 'events': bundle['events'],
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('committed', domain_payload(response))
        self.assertEqual(len(domain_payload(response)['acknowledgements']), 1)

    def test_capture_v2_runtime_declares_bounded_capability_only_when_authorized(self):
        attempt, _ = self.capture()
        url = reverse('student-runtime-question', args=[self.session.pk, attempt.assignment_id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(domain_payload(response)['process']['capture_bundle'], {
            'enabled': True, 'version': 1, 'max_events': 100,
            'max_checkpoints': 100, 'max_bytes': 8388608, 'mutation_receipts': True,
        })
        from apps.accounts.models import User
        from .models import Attempt, ExperimentSession, StudentQuestionAssignment
        from .test_support import accept_research_consent
        legacy_student = User.objects.create_user(username='CAPTURE-LEGACY', role=User.Role.STUDENT)
        accept_research_consent(legacy_student)
        self.client.force_login(legacy_student)
        legacy = ExperimentSession.objects.create(version=self.version, student=legacy_student)
        assignment = StudentQuestionAssignment.objects.create(session=legacy,
            snapshot=attempt.assignment.snapshot, item_instance=attempt.assignment.item_instance,
            assigned_sequence_index=1)
        Attempt.objects.create(session=legacy, assignment=assignment)
        url = reverse('student-runtime-question', args=[legacy.pk, assignment.pk])
        self.assertNotIn('capture_bundle', domain_payload(self.client.get(url))['process'])

    def test_capture_bundle_still_enforces_deadline_and_writer_lease(self):
        from .writer_lease_services import acquire_attempt_writer_lease
        attempt, bundle = self.capture()
        acquire_attempt_writer_lease(attempt, client_instance_id=uuid.uuid4())
        response = self.post('assessment_api:save_draft', self.draft_document(bundle), attempt)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).exists())
        self.session.deadline_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=['deadline_at'])
        response = self.post('assessment_process:capture_v2_batch', {'capture_bundle': bundle})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())


class BatchContextQueryTests(TestCase):
    setUp = CaptureBundleTests.setUp
    transition = CaptureBundleTests.transition
    bundle = CaptureBundleTests.bundle
    capture = CaptureBundleTests.capture
    tool_document = CaptureBundleTests.tool_document
    draft_document = CaptureBundleTests.draft_document

    def test_bundle_mutations_reuse_locked_session_and_attempt(self):
        from .capture_bundle_services import save_draft_capture_bundle, record_tool_capture_bundle
        for operation in ('draft', 'tool'):
            with self.subTest(operation=operation):
                attempt, bundle = self.capture()
                document = self.draft_document(bundle) if operation == 'draft' else self.tool_document(bundle)
                mutate = save_draft_capture_bundle if operation == 'draft' else record_tool_capture_bundle
                with CaptureQueriesContext(connection) as queries:
                    mutate(student=self.student, attempt_id=attempt.pk, document=document)
                for table, limit in (('assessment_experimentsession', 1 if operation == 'draft' else 2), ('assessment_attempt', 2)):
                    reads = [row['sql'] for row in queries if row['sql'].startswith('SELECT')
                             and not row['sql'].startswith('SELECT 1 ')
                             and f'FROM "{table}"' in row['sql']]
                    self.assertLessEqual(len(reads), limit, f'{operation} reloaded its owned/locked {table}')

    def test_checkpoint_context_and_identity_reads_are_constant_per_bundle(self):
        from .capture_bundle_services import ingest_capture_bundle
        _, bundle = self.capture()
        bundle['events'] = []
        def ingest(count):
            material = copy.deepcopy(bundle)
            material['checkpoints'] = [dict(material['checkpoints'][0], checkpoint_id=str(uuid.uuid4()))
                                       for _ in range(count)]
            with CaptureQueriesContext(connection) as queries:
                ingest_capture_bundle(student=self.student, document={'capture_bundle': material})
            return len([row for row in queries if row['sql'].startswith('SELECT') and any(
                f'FROM "{table}"' in row['sql'] for table in (
                    'assessment_experimentsession', 'assessment_clienttimesync', 'assessment_clientcapturecheckpoint'))])
        self.assertLessEqual(ingest(6), ingest(1), 'Identical checkpoint context needs one set of link/identity reads')
    def test_context_queries_do_not_grow_with_repeated_context(self):
        from .event_batch_services import parse_event_batch
        from .time_sync_services import create_pending_time_sync
        attempt, bundle = self.capture()
        events = [self.transition(attempt, None, 'A', sequence=sequence) for sequence in range(1, 7)]
        sample = create_pending_time_sync(session=self.session, client_instance_id=self.stream,
            client_wall_time_utc=timezone.now(), client_monotonic_ms=0,
            client_time_origin=timezone.datetime(2026, 9, 6, tzinfo=timezone.get_current_timezone()))
        for event in events:
            event['time_sync_id'] = str(sample.pk)
        def parse(rows):
            with CaptureQueriesContext(connection) as queries:
                parse_event_batch(student=self.student, document={
                    'client_instance_id': str(self.stream), 'events': rows,
                })
            return len(queries)
        self.assertLessEqual(parse(events), parse(events[:1]),
                             'Loading identical session/attempt context must be constant per batch')


class CaptureBundleCommitTests(TransactionTestCase):
    setUp = CaptureBundleTests.setUp
    transition = CaptureBundleTests.transition
    bundle = CaptureBundleTests.bundle
    capture = CaptureBundleTests.capture
    draft_document = CaptureBundleTests.draft_document
    tool_document = CaptureBundleTests.tool_document

    def test_session_lock_allows_independent_time_sync_foreign_key_commit(self):
        from .capture_bundle_services import _locked_session
        from .event_batch_services import batch_session_contexts
        from .models import ClientTimeSync
        from .time_sync_services import create_pending_time_sync

        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row-lock and deferred foreign-key semantics')

        def create_sample():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '500ms'")
                    now = timezone.now()
                    sample = create_pending_time_sync(session=self.session, client_instance_id=uuid.uuid4(),
                        client_wall_time_utc=now, client_monotonic_ms=100, client_time_origin=now)
                return sample.pk
            finally:
                close_old_connections()

        for path in ('bundle', 'event_batch'):
            with self.subTest(path=path), ThreadPoolExecutor(max_workers=1) as executor:
                with transaction.atomic():
                    if path == 'bundle':
                        _locked_session(self.student, self.session.pk)
                    else:
                        batch_session_contexts(student=self.student,
                            event_documents=[{'session_id': self.session.pk}], locked=True)
                    sample_id = executor.submit(create_sample).result(timeout=10)
                self.assertTrue(ClientTimeSync.objects.filter(pk=sample_id).exists())

    def test_session_mutation_locks_still_exclude_another_writer(self):
        from .capture_bundle_services import _locked_session

        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row-lock semantics')

        def second_writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '500ms'")
                    _locked_session(self.student, self.session.pk)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor, transaction.atomic():
            _locked_session(self.student, self.session.pk)
            with self.assertRaisesMessage(DatabaseError, 'lock timeout'):
                executor.submit(second_writer).result(timeout=10)

    def test_commit_failure_cannot_return_receipt_or_leave_partial_evidence(self):
        from .capture_bundle_services import ingest_capture_bundle
        _, bundle = self.capture()
        with patch.object(connection, 'commit', side_effect=DatabaseError('injected commit failure')):
            with self.assertRaises(DatabaseError):
                ingest_capture_bundle(student=self.student, document={'capture_bundle': bundle})
        self.assertFalse(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_lost_commit_response_replays_original_mutation_receipt_after_expiry(self):
        from .capture_bundle_services import save_draft_capture_bundle, record_tool_capture_bundle
        for kind in ('draft', 'tool'):
            with self.subTest(kind=kind):
                attempt, bundle = self.capture()
                document = self.draft_document(bundle) if kind == 'draft' else self.tool_document(bundle)
                if kind == 'draft':
                    from .writer_lease_services import acquire_attempt_writer_lease
                    lease = acquire_attempt_writer_lease(attempt, client_instance_id=self.stream)
                    document.update(writer_lease_token=str(lease.token), writer_client_instance_id=str(self.stream))
                mutate = save_draft_capture_bundle if kind == 'draft' else record_tool_capture_bundle
                with patch.object(connection, 'run_and_clear_commit_hooks', side_effect=RuntimeError('lost response')):
                    with self.assertRaises(RuntimeError):
                        mutate(student=self.student, attempt_id=attempt.pk, document=document)
                from .models import Attempt, CaptureMutationReceipt
                stored = CaptureMutationReceipt.objects.get(attempt=attempt, kind=kind).receipt
                Attempt.objects.filter(pk=attempt.pk).update(status=Attempt.Status.SUBMITTED)
                Attempt.objects.filter(pk=attempt.pk).update(writer_lease_expires_at=timezone.now() - timedelta(seconds=1))
                self.session.deadline_at = timezone.now() - timedelta(seconds=1)
                self.session.save(update_fields=['deadline_at'])
                with CaptureQueriesContext(connection) as queries:
                    replay = mutate(student=self.student, attempt_id=attempt.pk, document=document)
                receipt = replay[1] if kind == 'draft' else replay
                self.assertEqual(receipt, stored)
                self.assertFalse([q['sql'] for q in queries if q['sql'].startswith(('INSERT', 'UPDATE', 'DELETE'))])
                self.session.deadline_at = None
                self.session.save(update_fields=['deadline_at'])
                Attempt.objects.filter(pk=attempt.pk).update(status=Attempt.Status.DRAFT)

    def test_mutation_commit_failure_rolls_back_receipt_and_business(self):
        from .capture_bundle_services import save_draft_capture_bundle
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        with patch.object(connection, 'commit', side_effect=DatabaseError('injected commit failure')):
            with self.assertRaises(DatabaseError):
                save_draft_capture_bundle(student=self.student, attempt_id=attempt.pk, document=document)
        from .models import CaptureMutationReceipt
        self.assertFalse(CaptureMutationReceipt.objects.exists())
        self.assertFalse(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).exists())
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 0)

    def test_concurrent_duplicate_mutation_has_one_receipt_and_saved_version(self):
        from .capture_bundle_services import save_draft_capture_bundle
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        barrier = Barrier(2)
        def save():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return save_draft_capture_bundle(student=self.student, attempt_id=attempt.pk,
                                                  document=copy.deepcopy(document))[1]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = [future.result(timeout=30) for future in [executor.submit(save), executor.submit(save)]]
        self.assertEqual(receipts[0], receipts[1])
        from .models import CaptureMutationReceipt
        self.assertEqual(CaptureMutationReceipt.objects.filter(attempt=attempt).count(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 1)
        self.assertEqual(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).count(), 1)


class CaptureMutationReceiptTests(TestCase):
    setUp = CaptureBundleTests.setUp
    transition = CaptureBundleTests.transition
    bundle = CaptureBundleTests.bundle
    capture = CaptureBundleTests.capture
    draft_document = CaptureBundleTests.draft_document
    tool_document = CaptureBundleTests.tool_document
    post = CaptureBundleTests.post
    assert_receipt = CaptureBundleTests.assert_receipt

    def test_draft_replay_preserves_original_head_version_and_saved_at(self):
        from .models import Attempt
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        original = self.assert_receipt(self.post('assessment_api:save_draft', document, attempt), bundle)
        Attempt.objects.filter(pk=attempt.pk).update(status=Attempt.Status.SUBMITTED, response_version=9)
        self.session.deadline_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=['deadline_at'])
        replay = self.assert_receipt(self.post('assessment_api:save_draft', document, attempt), bundle)
        self.assertEqual(replay, original)

    def test_token_material_and_kind_conflicts_never_add_evidence(self):
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        self.assert_receipt(self.post('assessment_api:save_draft', document, attempt), bundle)
        altered = copy.deepcopy(document)
        altered['answer'] = 'silently ignored raw answer'
        response = self.post('assessment_api:save_draft', altered, attempt)
        self.assertEqual(response.status_code, 409, response.content)
        tool = self.tool_document(bundle)
        tool['operation_id'] = document['capture_operation_id']
        response = self.post('assessment_process:research_tools', tool, attempt)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(AssessmentEvent.objects.filter(pk=tool['operation_id']).exists())

    def test_replay_ignores_only_renewed_lease_credentials(self):
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        original = self.assert_receipt(self.post('assessment_api:save_draft', document, attempt), bundle)
        retry = copy.deepcopy(document)
        retry.update(writer_lease_token=str(uuid.uuid4()), writer_client_instance_id=str(uuid.uuid4()))
        self.assertEqual(self.assert_receipt(self.post('assessment_api:save_draft', retry, attempt), bundle), original)
        from .models import CaptureMutationReceipt
        stored = CaptureMutationReceipt.objects.get(attempt=attempt)
        self.assertEqual(stored.receipt, original)
        self.assertNotIn('capture_bundle', stored.receipt)
        self.assertNotIn('writer_lease_token', stored.receipt)

    def test_unseen_expired_operation_still_rejects(self):
        attempt, bundle = self.capture()
        self.session.deadline_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=['deadline_at'])
        response = self.post('assessment_api:save_draft', self.draft_document(bundle), attempt)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(AssessmentEvent.objects.filter(pk=bundle['events'][0]['event_id']).exists())

    def test_draft_bundle_requires_operation_id(self):
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        document.pop('capture_operation_id')
        response = self.post('assessment_api:save_draft', document, attempt)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())

    def test_current_authorization_can_deny_a_committed_receipt(self):
        from apps.accounts.models import Course, CourseEnrollment, ClassGroup, ResearchConsent
        attempt, bundle = self.capture()
        document = self.draft_document(bundle)
        course = Course.objects.create(owner=self.teacher, code='CAPTURE-RECEIPT', term='2026', name='Capture')
        group = ClassGroup.objects.create(owner=self.teacher, course=course, name='Capture class')
        enrollment = CourseEnrollment.objects.create(course=course, class_group=group, student=self.student)
        experiment = self.version.experiment
        experiment.course = course
        experiment.save(update_fields=['course'])
        self.assert_receipt(self.post('assessment_api:save_draft', document, attempt), bundle)
        CourseEnrollment.objects.filter(pk=enrollment.pk).update(is_active=False)
        response = self.post('assessment_api:save_draft', document, attempt)
        self.assertEqual(response.status_code, 409, response.content)
        CourseEnrollment.objects.filter(pk=enrollment.pk).update(is_active=True)
        ResearchConsent.objects.filter(student=self.student).delete()
        response = self.post('assessment_api:save_draft', document, attempt)
        self.assertEqual(response.status_code, 403, response.content)


class CaptureBundleSchemaTests(TestCase):
    def test_schema_documents_optional_capture_without_changing_frozen_envelopes(self):
        from .api.mutation_schema import BUNDLE, MUTATIONS, PROCESS
        self.assertIn('expected_version', BUNDLE['required'])
        draft = MUTATIONS['save_draft']['request']['oneOf'][0]
        self.assertIn('capture_bundle', draft['properties'])
        bundle = draft['properties']['capture_bundle']
        self.assertIn('capture_operation_id', draft['properties'])
        self.assertNotIn('expected_version', bundle['properties'])
        self.assertEqual(set(bundle['required']), {'version', 'client_instance_id', 'event_boundary', 'events', 'checkpoints'})
        self.assertIn('capture_bundle', PROCESS['research_tools']['request']['oneOf'][1]['properties'])
        self.assertEqual(PROCESS['capture_v2_batch']['request']['oneOf'][1]['required'], ['capture_bundle'])
