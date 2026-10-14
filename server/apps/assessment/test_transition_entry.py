"""A confirmed source operation survives independently of target entry."""
import copy
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.api.test_support import domain_payload
from apps.question_bank.models import Question
from .models import AssessmentEvent, Attempt, ExperimentSession, NavigationReceipt, QuestionVisitEvent
from .test_student_api import StudentApiFixtureMixin
from . import test_saved_navigation as navigation_fixtures
from . import test_close_capture_bundle as close_fixtures


def next_entry_material(assignment_id):
    return {'assignment_id': assignment_id, 'navigation_id': str(uuid.uuid4()),
            'reason': 'navigation_click', 'client_occurred_at': timezone.now().isoformat(),
            'client_monotonic_ms': '123.456', 'client_time_origin': '2026-10-13T00:00:00Z'}


@override_settings(SAVE_AND_LEAVE_ENABLED=True, SAVE_AND_LEAVE_USER_IDS=frozenset({'*'}))
class SavedTransitionEntryTests(TestCase):
    setUp = navigation_fixtures.SavedNavigationTests.setUp
    transition = navigation_fixtures.SavedNavigationTests.transition
    bundle = navigation_fixtures.SavedNavigationTests.bundle
    post = navigation_fixtures.SavedNavigationTests.post

    def document(self):
        attempt, document = self.bundle()
        # Test fixtures select a protocol when creating their already-started session.
        ExperimentSession.objects.filter(pk=self.session.pk).update(transition_protocol_version=1)
        document['next_entry'] = next_entry_material(document['to_assignment_id'])
        return attempt, document

    def test_save_then_entry_confirms_draft_and_only_one_real_target_visit(self):
        attempt, document = self.document()
        first = self.post(attempt, document)
        self.assertEqual(first.status_code, 200, first.content)
        receipt = domain_payload(first)
        self.assertTrue(receipt['committed'])
        self.assertTrue(receipt['next_entry']['committed'])
        runtime = receipt['next_entry']['runtime']
        self.assertEqual(runtime['assignment']['assignment_id'], document['to_assignment_id'])
        self.assertEqual(runtime['entry_receipt']['navigation_id'], document['next_entry']['navigation_id'])
        self.assertEqual(runtime['process']['visit_id'], runtime['entry_receipt']['visit_id'])
        self.assertNotIn('correct_answer', runtime['question'])
        self.assertNotIn('explanation', runtime['question'])
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.DRAFT)
        self.assertEqual(attempt.draft_answer, 'A')
        replay = domain_payload(self.post(attempt, document))
        self.assertEqual(replay['next_entry']['runtime']['entry_receipt'], runtime['entry_receipt'])
        self.assertEqual(QuestionVisitEvent.objects.filter(attempt__assignment_id=document['to_assignment_id']).count(), 1)
        self.assertEqual(NavigationReceipt.objects.filter(attempt=attempt).count(), 1)

    def test_failed_entry_preserves_confirmed_source_and_retry_enters_only_once(self):
        attempt, document = self.document()
        with patch('apps.assessment.api.views.record_visit_entry', side_effect=ValidationError('injected entry failure')):
            response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['committed'])
        self.assertTrue(receipt['writer_lease_released'])
        self.assertFalse(receipt['next_entry']['committed'])
        self.assertEqual(receipt['next_entry']['error']['code'], 'SESSION_NOT_ENTERABLE')
        attempt.refresh_from_db()
        self.assertEqual(attempt.draft_answer, 'A')
        self.assertEqual(attempt.response_version, 1)
        self.assertIsNone(attempt.writer_lease_token)
        self.assertFalse(QuestionVisitEvent.objects.filter(attempt__assignment_id=document['to_assignment_id']).exists())
        recovered = domain_payload(self.post(attempt, document))
        self.assertTrue(recovered['next_entry']['committed'])
        attempt.refresh_from_db()
        self.assertEqual(attempt.response_version, 1)

    def test_changed_entry_material_conflicts_without_another_visit(self):
        attempt, document = self.document()
        self.assertEqual(self.post(attempt, document).status_code, 200)
        changed = copy.deepcopy(document)
        changed['next_entry']['navigation_id'] = str(uuid.uuid4())
        response = self.post(attempt, changed)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=self.session).count(), 1)

    def test_target_lookup_failure_returns_the_confirmed_source_receipt(self):
        attempt, document = self.document()
        with patch('apps.assessment.transition_entry_services.Attempt.objects.values_list',
                   side_effect=DatabaseError('injected target lookup failure')):
            response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['next_entry']['error']['code'], 'ENTRY_OUTCOME_UNKNOWN')
        attempt.refresh_from_db()
        self.assertEqual(attempt.draft_answer, 'A')
        self.assertEqual(NavigationReceipt.objects.filter(attempt=attempt).count(), 1)

    def test_malformed_or_mismatched_entry_does_not_commit_source(self):
        for fault in ('target', 'clock', 'unknown'):
            with self.subTest(fault=fault):
                attempt, document = self.document()
                if fault == 'target':
                    document['next_entry']['assignment_id'] = attempt.assignment_id
                elif fault == 'clock':
                    document['next_entry']['client_monotonic_ms'] = '-1'
                else:
                    document['next_entry']['prefetch'] = True
                response = self.post(attempt, document)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertFalse(NavigationReceipt.objects.filter(attempt=attempt).exists())
                self.assertFalse(QuestionVisitEvent.objects.filter(session=self.session).exists())

    def test_existing_protocol_cannot_be_upgraded_by_a_request(self):
        attempt, document = self.document()
        ExperimentSession.objects.filter(pk=self.session.pk).update(transition_protocol_version=0)
        response = self.post(attempt, document)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(NavigationReceipt.objects.filter(attempt=attempt).exists())

    def test_old_navigation_material_fingerprint_stays_unchanged(self):
        from .atomic_submission_services import process_material_fingerprint
        attempt, document = self.bundle()
        self.assertEqual(self.post(attempt, document).status_code, 200)
        old_material = {key: document.get(key) for key in (
            'save_and_leave', 'leave_event_id', 'target_event_id', 'to_assignment_id', 'client_clicked_at')}
        self.assertEqual(NavigationReceipt.objects.get(attempt=attempt).material_fingerprint,
                         process_material_fingerprint(old_material))

    def test_six_response_types_keep_their_original_draft_under_transition_entry(self):
        values = {Question.Type.SINGLE: 'A', Question.Type.MULTIPLE: ['A', 'B'], Question.Type.TRUE_FALSE: 'true',
                  Question.Type.FILL_BLANK: '填空', Question.Type.NUMERIC: '2', Question.Type.SHORT: '短答'}
        for kind, value in values.items():
            with self.subTest(kind=kind):
                _, document = self.document()
                attempt = self.attempts[kind]
                destination = self.attempts[Question.Type.SINGLE if kind == Question.Type.SHORT else Question.Type.SHORT].assignment_id
                empty = [] if kind == Question.Type.MULTIPLE else ('' if kind in {
                    Question.Type.FILL_BLANK, Question.Type.NUMERIC, Question.Type.SHORT} else None)
                transition = self.transition(attempt, empty, value)
                document['save_and_leave']['events'][0] = transition
                document['save_and_leave']['events'][1]['attempt_id'] = attempt.pk
                document['save_and_leave']['events'][1]['payload']['to_assignment_id'] = destination
                document['target_event_id'] = transition['event_id']
                document['to_assignment_id'] = destination
                document['next_entry']['assignment_id'] = destination
                response = self.post(attempt, document)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertTrue(domain_payload(response)['next_entry']['committed'])
                attempt.refresh_from_db()
                self.assertEqual(attempt.draft_answer, value)
                self.assertEqual(attempt.status, Attempt.Status.DRAFT)
                self.assertIsNone(attempt.final_response_event_id)


@override_settings(CLOSE_BUNDLE_ENABLED=True, CAPTURE_BUNDLE_ENABLED=True)
class ClosedTransitionEntryTests(TestCase):
    setUp = close_fixtures.CloseCaptureBundleTests.setUp
    transition = close_fixtures.CloseCaptureBundleTests.transition
    bundle = close_fixtures.CloseCaptureBundleTests.bundle
    capture = close_fixtures.CloseCaptureBundleTests.capture
    close_document = close_fixtures.CloseCaptureBundleTests.close_document
    post = close_fixtures.CloseCaptureBundleTests.post

    def document(self):
        attempt, document = self.close_document()
        ExperimentSession.objects.filter(pk=self.session.pk).update(transition_protocol_version=1)
        document['next_entry'] = next_entry_material(self.attempts[Question.Type.SHORT].assignment_id)
        return attempt, document

    def test_close_then_entry_preserves_source_receipt_and_replays_target(self):
        attempt, document = self.document()
        response = self.post('assessment_process:writer_lease_release', document, attempt)
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['committed'])
        self.assertTrue(receipt['writer_lease_released'])
        self.assertTrue(receipt['next_entry']['committed'])
        replay = domain_payload(self.post('assessment_process:writer_lease_release', document, attempt))
        self.assertEqual(replay['next_entry']['runtime']['entry_receipt'], receipt['next_entry']['runtime']['entry_receipt'])
        self.assertEqual(QuestionVisitEvent.objects.filter(session=self.session).count(), 1)

    def test_close_commit_survives_target_failure(self):
        attempt, document = self.document()
        with patch('apps.assessment.api.views.record_visit_entry', side_effect=ValidationError('target failed')):
            response = self.post('assessment_process:writer_lease_release', document, attempt)
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['committed'])
        self.assertFalse(receipt['next_entry']['committed'])
        attempt.refresh_from_db()
        self.assertIsNone(attempt.writer_lease_token)
        self.assertTrue(AssessmentEvent.objects.filter(pk=document['leave_event_id']).exists())


@override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}),
                   ASSESSMENT_TRANSITION_ENABLED=True, ASSESSMENT_TRANSITION_USER_IDS=frozenset({'*'}))
class TransitionProtocolTests(StudentApiFixtureMixin, TestCase):
    def test_new_session_freezes_protocol_and_switch_off_does_not_change_it(self):
        response = self.start()
        self.assertEqual(response.status_code, 201, response.content)
        session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        self.assertEqual(getattr(session, 'transition_protocol_version', 0), 1)
        url = reverse('student-runtime-question', args=[session.pk, session.assignments.get().pk])
        with override_settings(ASSESSMENT_TRANSITION_ENABLED=False):
            self.assertEqual(domain_payload(self.client.get(url))['process']['transition_entry'],
                             {'enabled': True, 'version': 1})

    def test_disabled_or_not_allowlisted_new_session_keeps_existing_protocol(self):
        with override_settings(ASSESSMENT_TRANSITION_ENABLED=False):
            self.assertEqual(self.start().status_code, 201)
        session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        self.assertEqual(session.transition_protocol_version, 0)
        url = reverse('student-runtime-question', args=[session.pk, session.assignments.get().pk])
        self.assertNotIn('transition_entry', domain_payload(self.client.get(url))['process'])

    @override_settings(ASSESSMENT_TRANSITION_USER_IDS=frozenset())
    def test_new_session_outside_allowlist_keeps_existing_protocol(self):
        self.assertEqual(self.start().status_code, 201)
        session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        self.assertEqual(session.transition_protocol_version, 0)

    def test_pinned_protocol_cannot_change_after_start(self):
        self.assertEqual(self.start().status_code, 201)
        session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        session.transition_protocol_version = 0
        with self.assertRaisesMessage(ValidationError, 'transition protocol is immutable'):
            session.save()


@override_settings(SAVE_AND_LEAVE_ENABLED=True, SAVE_AND_LEAVE_USER_IDS=frozenset({'*'}))
class TransitionTransactionTests(TransactionTestCase):
    setUp = navigation_fixtures.SavedNavigationTests.setUp
    transition = navigation_fixtures.SavedNavigationTests.transition
    bundle = navigation_fixtures.SavedNavigationTests.bundle
    post = navigation_fixtures.SavedNavigationTests.post
    document = SavedTransitionEntryTests.document

    def test_source_is_durable_before_target_entry_transaction_begins(self):
        from .api.views import StudentRuntimeQuestionEntryView
        attempt, document = self.document()
        enter = StudentRuntimeQuestionEntryView.enter
        def enter_after_source(view, *args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(NavigationReceipt.objects.filter(attempt=attempt).count(), 1)
            self.assertEqual(Attempt.objects.get(pk=attempt.pk).draft_answer, 'A')
            return enter(view, *args, **kwargs)
        with patch.object(StudentRuntimeQuestionEntryView, 'enter', enter_after_source):
            response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(domain_payload(response)['next_entry']['committed'])

    def test_deadline_rechecked_after_source_commit_without_extending_it(self):
        from .api.views import StudentRuntimeQuestionEntryView
        attempt, document = self.document()
        deadline = timezone.now() + timedelta(seconds=60)
        ExperimentSession.objects.filter(pk=self.session.pk).update(deadline_at=deadline)
        enter = StudentRuntimeQuestionEntryView.enter
        def enter_after_deadline(view, *args, **kwargs):
            with patch('apps.assessment.api.views.timezone.now', return_value=deadline + timedelta(seconds=1)):
                return enter(view, *args, **kwargs)
        with patch.object(StudentRuntimeQuestionEntryView, 'enter', enter_after_deadline):
            response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['committed'])
        self.assertFalse(receipt['next_entry']['committed'])
        self.assertEqual(Attempt.objects.get(pk=attempt.pk).draft_answer, 'A')
        self.assertEqual(ExperimentSession.objects.get(pk=self.session.pk).deadline_at, deadline)
        self.assertFalse(QuestionVisitEvent.objects.filter(session=self.session).exists())

    def test_target_commit_response_failure_is_unknown_and_original_entry_recovers(self):
        from .api.views import StudentRuntimeQuestionEntryView
        attempt, document = self.document()
        enter = StudentRuntimeQuestionEntryView.enter
        def lose_after_target_commit(view, *args, **kwargs):
            with patch.object(connection, 'run_and_clear_commit_hooks', side_effect=DatabaseError('injected post-commit loss')):
                return enter(view, *args, **kwargs)
        with patch.object(StudentRuntimeQuestionEntryView, 'enter', lose_after_target_commit):
            response = self.post(attempt, document)
        self.assertEqual(response.status_code, 200, response.content)
        receipt = domain_payload(response)
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['next_entry']['error']['code'], 'ENTRY_OUTCOME_UNKNOWN')
        self.assertEqual(QuestionVisitEvent.objects.filter(session=self.session).count(), 1)
        target = Attempt.objects.get(assignment_id=document['to_assignment_id'])
        first_opened_at = target.first_opened_at
        recovered = domain_payload(self.post(attempt, document))
        self.assertTrue(recovered['next_entry']['committed'])
        self.assertEqual(QuestionVisitEvent.objects.filter(session=self.session).count(), 1)
        target.refresh_from_db()
        self.assertEqual(target.first_opened_at, first_opened_at)

    def test_concurrent_replays_produce_one_source_receipt_and_one_target_visit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from types import SimpleNamespace
        from .saved_navigation_services import save_and_leave_attempt
        from .transition_entry_services import append_next_entry
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locks')
        attempt, document = self.document()
        barrier = Barrier(2)
        def replay():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                _, receipt = save_and_leave_attempt(student=self.student, attempt_id=attempt.pk, document=document)
                return append_next_entry(SimpleNamespace(user=self.student), attempt.pk, document, receipt)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: replay(), range(2)))
        self.assertTrue(all(row['next_entry']['committed'] for row in results))
        self.assertEqual(NavigationReceipt.objects.filter(attempt=attempt).count(), 1)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=self.session).count(), 1)
        self.assertEqual(Attempt.objects.get(pk=attempt.pk).response_version, 1)
