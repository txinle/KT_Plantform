"""Ordinary navigation must preserve a draft and real process evidence together."""
from apps.api.test_support import domain_payload
from copy import deepcopy
import json
import uuid
from time import perf_counter
from datetime import timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from django.db import DatabaseError, close_old_connections, connection
from django.test.utils import CaptureQueriesContext
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.question_bank.models import Question
from . import test_capture_v2 as capture_fixtures
from . import test_atomic_submission as atomic_fixtures
from .test_student_api import StudentApiFixtureMixin
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

    def test_navigation_rejection_is_durable_and_binds_inline_confidence_material(self):
        from .models import CaptureMutationReceipt
        attempt, document = self.bundle()
        document['target_event_id'] = str(uuid.uuid4())
        document['inline_confidence'] = None
        first = self.post(attempt, document)
        self.assertEqual(first.status_code, 409, first.content)
        error = domain_payload(first)['error']
        error.pop('request_id', None)
        self.assertEqual(error['code'], 'response_target_not_persisted')
        self.assertTrue(error['fields']['mutation_rejected'])
        self.assertEqual(error['fields']['mutation_kind'], 'navigation')
        self.assertEqual(error['fields']['navigation_token'], document['navigation_token'])
        self.assertEqual(error['fields']['attempt_id'], attempt.pk)
        self.assertTrue(CaptureMutationReceipt.objects.filter(attempt=attempt,
            token=document['navigation_token']).exists())
        self.assertFalse(AssessmentEvent.objects.filter(event_type__in=['response_transition', 'item_left']).exists())
        self.assertFalse(ClientCaptureCheckpoint.objects.exists())
        with patch('apps.assessment.saved_navigation_services.ingest_capture_batch', side_effect=AssertionError('must not retry')):
            replay = self.post(attempt, document)
        replay_error = domain_payload(replay)['error']
        replay_error.pop('request_id', None)
        self.assertEqual(replay_error, error)
        changed = deepcopy(document)
        changed.pop('inline_confidence')
        conflict = self.post(attempt, changed)
        self.assertEqual(domain_payload(conflict)['error']['code'], 'navigation_material_conflict')
        self.assertFalse(domain_payload(conflict)['error']['fields'].get('mutation_rejected'))

    def test_new_navigation_receipt_freezes_inline_confidence_and_legacy_replay_keeps_old_formula(self):
        from .atomic_submission_services import process_material_fingerprint
        attempt, document = self.bundle()
        document['inline_confidence'] = None
        original = self.post(attempt, document)
        self.assertEqual(original.status_code, 200, original.content)
        self.assertEqual(domain_payload(original)['receipt_version'], 2)
        altered = deepcopy(document)
        altered.pop('inline_confidence')
        self.assertEqual(self.post(attempt, altered).status_code, 409)
        # Existing version-1 outcomes remain recoverable with their original material formula.
        stored = NavigationReceipt.objects.get(attempt=attempt)
        stored.receipt.pop('receipt_version')
        material = {key: document.get(key) for key in ['save_and_leave', 'leave_event_id',
            'target_event_id', 'to_assignment_id', 'client_clicked_at']}
        stored.material_fingerprint = process_material_fingerprint(material)
        stored.save(update_fields=['receipt', 'material_fingerprint'])
        replay = self.post(attempt, altered)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(domain_payload(replay), stored.receipt)

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

    def test_navigation_reuses_each_locked_row_and_returns_current_receipt_fields(self):
        from .saved_navigation_services import save_and_leave_attempt
        attempt, document = self.bundle()
        sql_seconds = 0

        def measure(execute, sql, params, many, context):
            nonlocal sql_seconds
            started = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                sql_seconds += perf_counter() - started

        started = perf_counter()
        with connection.execute_wrapper(measure), CaptureQueriesContext(connection) as queries:
            saved, receipt = save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=document)
        elapsed = perf_counter() - started
        print(f'F3_DIAGNOSTIC queries={len(queries)} sql_ms={sql_seconds * 1000:.3f} '
              f'application_ms={(elapsed - sql_seconds) * 1000:.3f} total_ms={elapsed * 1000:.3f}')
        for table in ('assessment_experimentsession', 'assessment_attempt'):
            locks = [row['sql'] for row in queries if f'FROM "{table}"' in row['sql']
                     and ('FOR UPDATE' in row['sql'] or 'FOR NO KEY UPDATE' in row['sql'])]
            self.assertEqual(len(locks), 1, '\n'.join(locks))
        self.assertEqual(receipt['response_version'], saved.response_version)
        self.assertEqual(receipt['active_response_event_id'], str(saved.active_response_event_id))
        self.assertEqual(receipt['saved_at'], saved.draft_saved_at.isoformat())
        self.assertEqual(receipt['status'], saved.status)
        self.assertIsNone(saved.writer_lease_token)
        saved.refresh_from_db()
        self.assertEqual(receipt['response_version'], saved.response_version)
        self.assertEqual(receipt['saved_at'], saved.draft_saved_at.isoformat())

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

    def test_rejection_commit_failure_is_unknown_without_durable_proof(self):
        from .saved_navigation_services import save_and_leave_attempt
        from .models import CaptureMutationReceipt
        attempt, document = self.bundle()
        document['target_event_id'] = str(uuid.uuid4())
        with patch.object(connection, 'commit', side_effect=DatabaseError('injected commit failure')):
            with self.assertRaises(DatabaseError) as outcome:
                save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertFalse(hasattr(outcome.exception, 'mutation_rejection_receipt'))
        self.assertFalse(CaptureMutationReceipt.objects.exists())
        self.assertFalse(NavigationReceipt.objects.exists())

    def test_post_commit_rejection_response_loss_replays_durable_original_rejection(self):
        from .saved_navigation_services import save_and_leave_attempt
        from .event_batch_services import ProcessRequestError
        from .models import CaptureMutationReceipt
        attempt, document = self.bundle()
        document['target_event_id'] = str(uuid.uuid4())
        with patch.object(connection, 'run_and_clear_commit_hooks', side_effect=ValidationError('lost response')):
            with self.assertRaises(ValidationError) as outcome:
                save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=document)
        self.assertFalse(hasattr(outcome.exception, 'mutation_rejection_receipt'))
        self.assertFalse(hasattr(outcome.exception, 'navigation_rejection_token'))
        stored = CaptureMutationReceipt.objects.get(attempt=attempt).receipt
        with self.assertRaises(ProcessRequestError) as replay:
            save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=deepcopy(document))
        self.assertEqual(replay.exception.mutation_rejection_receipt, stored)
        self.assertFalse(NavigationReceipt.objects.exists())

    def test_navigation_racing_formal_submission_never_overwrites_the_final_response(self):
        from .capture_v2_services import ingest_capture_batch
        from .saved_navigation_services import save_and_leave_attempt
        from .services import save_draft, submit_attempt
        attempt, document = self.bundle()
        stream = document['save_and_leave']['client_instance_id']
        ingest_capture_batch(student=self.student, document={'client_instance_id': stream,
            'events': [document['save_and_leave']['events'][0]]})
        save_draft(attempt.pk, None, target_event_id=document['target_event_id'], expected_version=0)
        document['save_and_leave']['expected_version'] = 1
        barrier = Barrier(2)

        def mutate(navigation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if navigation:
                    return save_and_leave_attempt(student=self.student, attempt_id=attempt.pk,
                        document=deepcopy(document))[1]
                return submit_attempt(attempt.pk, uuid.uuid4(), target_event_id=document['target_event_id'])
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            saved = executor.submit(mutate, True)
            submitted = executor.submit(mutate, False)
            receipt = saved.result(timeout=30)
            submitted.result(timeout=30)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.SUBMITTED)
        self.assertEqual(str(attempt.final_response_event_id), document['target_event_id'])
        self.assertEqual(attempt.response_version, 1)
        self.assertEqual(receipt['response_version'], 1)
        self.assertEqual(SubmissionEvent.objects.filter(attempt=attempt).count(), 1)
        self.assertEqual(NavigationReceipt.objects.filter(attempt=attempt).count(), 1)

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


@override_settings(SAVE_AND_LEAVE_ENABLED=True, SAVE_AND_LEAVE_USER_IDS=frozenset({'*'}),
                   CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}),
                   ASSESSMENT_TRANSITION_ENABLED=False)
class SavedNavigationTeacherCloseRaceTests(StudentApiFixtureMixin, TransactionTestCase):
    transition = capture_fixtures.CaptureV2Tests.transition
    bundle = SavedNavigationTests.bundle

    def setUp(self):
        super().setUp()
        from apps.question_bank.models import QuestionConcept
        from .services import get_or_create_session, record_visit
        destination = Question.objects.create(bank=self.bank, sequence=2, code='NAV-DESTINATION',
            question_type=Question.Type.SHORT, text='下一题', correct_answer='答案')
        QuestionConcept.objects.create(question=destination, concept=self.question.primary_concept, is_primary=True)
        self.experiment, self.task = self.publish_task(name='导航关闭竞争', feedback_mode=self.feedback_mode)
        self.session = get_or_create_session(self.experiment, self.student, class_task=self.task)
        self.attempts = {assignment.snapshot.question_type: record_visit(self.session.pk, assignment.pk)
                         for assignment in self.session.assignments.select_related('snapshot')}

    def test_preclose_navigation_commits_while_teacher_close_commits_and_replays_afterward(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL concurrent transactions')
        from apps.experiments.publication_services import database_now, manual_close_task
        from .saved_navigation_services import save_and_leave_attempt
        from .services import _validate_process_event_time
        attempt, document = self.bundle()
        arrived_at = database_now()
        navigation_locked = Event()
        teacher_closed = Event()

        def wait_for_teacher_close(*args, **kwargs):
            # The navigation transaction already holds its session and attempt locks.
            navigation_locked.set()
            self.assertTrue(teacher_closed.wait(timeout=10), 'Teacher close could not commit during navigation')
            return _validate_process_event_time(*args, **kwargs)

        def navigate():
            close_old_connections()
            try:
                return save_and_leave_attempt(student=self.student, attempt_id=attempt.pk,
                    document=deepcopy(document), request_received_at=arrived_at)[1]
            finally:
                close_old_connections()

        def close_task():
            close_old_connections()
            try:
                self.assertTrue(navigation_locked.wait(timeout=10), 'Navigation did not reach its locked transaction')
                closed = manual_close_task(self.task, self.teacher, reason='teacher_ended')
                # manual_close_task's atomic transaction has committed before navigation proceeds.
                teacher_closed.set()
                return closed.manual_closed_at
            finally:
                close_old_connections()

        with patch('apps.assessment.saved_navigation_services._validate_process_event_time', wait_for_teacher_close):
            with ThreadPoolExecutor(max_workers=2) as pool:
                saved = pool.submit(navigate)
                closed = pool.submit(close_task)
                receipt = saved.result(timeout=30)
                closed_at = closed.result(timeout=30)
        self.assertLess(arrived_at, closed_at)
        self.assertTrue(receipt['committed'])
        attempt.refresh_from_db()
        self.assertEqual(attempt.draft_answer, 'A')
        self.assertEqual(attempt.response_version, 1)
        self.assertIsNone(attempt.writer_lease_token)
        original_events = list(AssessmentEvent.objects.filter(pk__in=[
            row['event_id'] for row in document['save_and_leave']['events']]).values_list('pk', 'payload'))
        self.assertEqual(len(original_events), 2)
        self.assertEqual(ClientCaptureCheckpoint.objects.filter(session=self.session).count(), 1)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        _, replay = save_and_leave_attempt(student=self.student, attempt_id=attempt.pk,
            document=deepcopy(document), request_received_at=database_now())
        self.assertEqual(replay, receipt)

        later = deepcopy(document)
        later['navigation_token'] = str(uuid.uuid4())
        later['save_and_leave']['events'][-1]['correlation_id'] = later['navigation_token']
        with self.assertRaisesMessage(ValidationError, '班级任务已结束') as rejected:
            save_and_leave_attempt(student=self.student, attempt_id=attempt.pk,
                document=later, request_received_at=database_now())
        self.assertEqual(rejected.exception.navigation_rejection_token, later['navigation_token'])
        self.assertEqual(NavigationReceipt.objects.filter(attempt=attempt).count(), 1)
        self.assertEqual(list(AssessmentEvent.objects.filter(pk__in=[row[0] for row in original_events])
            .values_list('pk', 'payload')), original_events)
