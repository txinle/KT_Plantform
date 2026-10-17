"""The content read must not establish a formal question entry."""
import uuid
from unittest.mock import patch
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.question_bank.models import Question, QuestionConcept, QuestionOption
from apps.api.test_support import domain_payload

from .models import AssessmentEvent, Attempt, ExperimentSession, QuestionVisitEvent, SubmissionEvent
from .test_student_api import StudentApiFixtureMixin


class StudentQuestionContentReadTests(StudentApiFixtureMixin, TestCase):
    def content_fixture(self):
        started = self.start()
        self.assertEqual(started.status_code, 201, started.content)
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        assignment = session.assignments.get()
        return session, assignment, f'/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/'

    def test_offline_identity_is_bound_to_the_authorized_session_and_assignment(self):
        session, assignment, url = self.content_fixture()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['offline_authorization'], {
            'user_id': self.student.pk, 'session_id': session.pk, 'assignment_id': assignment.pk,
        })

    def test_content_get_does_not_create_visits_or_start_attempt_timing(self):
        session, assignment, url = self.content_fixture()
        attempt = assignment.attempt
        session_fields = ('status', 'current_assignment_id', 'first_open_count', 'visit_event_count',
                          'submission_count', 'last_activity_at', 'deadline_at')
        attempt_fields = ('status', 'first_open_sequence_index', 'first_opened_at', 'last_opened_at',
                          'visit_count', 'active_seconds', 'item_started_at', 'item_deadline_at',
                          'initial_effective_deadline_at', 'current_effective_deadline_at')
        before_session = ExperimentSession.objects.filter(pk=session.pk).values(*session_fields).get()
        before_attempt = Attempt.objects.filter(pk=attempt.pk).values(*attempt_fields).get()
        before_visits = QuestionVisitEvent.objects.filter(session=session).count()
        before_entries = AssessmentEvent.objects.filter(session=session, event_type='item_entered').count()

        for _ in range(2):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()['data']['question']['snapshot_id'], assignment.snapshot_id)
            self.assertIsNone(response.json()['data']['process']['visit_id'])
            self.assertNotIn('entry_receipt', response.json()['data'])
        with self.subTest('visits'):
            self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), before_visits)
        with self.subTest('entry_events'):
            self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), before_entries)
        with self.subTest('session_state'):
            self.assertEqual(ExperimentSession.objects.filter(pk=session.pk).values(*session_fields).get(), before_session)
        with self.subTest('attempt_timing'):
            self.assertEqual(Attempt.objects.filter(pk=attempt.pk).values(*attempt_fields).get(), before_attempt)

    def test_expired_content_get_does_not_terminate_or_auto_submit(self):
        session, assignment, url = self.content_fixture()
        ExperimentSession.objects.filter(pk=session.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        before_session = ExperimentSession.objects.filter(pk=session.pk).values().get()
        before_attempt = Attempt.objects.filter(assignment=assignment).values().get()
        before_events = AssessmentEvent.objects.filter(session=session).count()
        before_submissions = SubmissionEvent.objects.filter(attempt__session=session).count()
        response = self.client.get(url)
        with self.subTest('authorized_read'):
            self.assertEqual(response.status_code, 200, response.content)
            self.assertFalse(response.json()['data']['entry_allowed'])
            self.assertEqual(response.json()['data']['entry_denial_code'], 'DEADLINE_EXPIRED')
        with self.subTest('session_unchanged'):
            self.assertEqual(ExperimentSession.objects.filter(pk=session.pk).values().get(), before_session)
        with self.subTest('attempt_unchanged'):
            self.assertEqual(Attempt.objects.filter(assignment=assignment).values().get(), before_attempt)
        with self.subTest('no_termination_events'):
            self.assertEqual(AssessmentEvent.objects.filter(session=session).count(), before_events)
        with self.subTest('no_automatic_submission'):
            self.assertEqual(SubmissionEvent.objects.filter(attempt__session=session).count(), before_submissions)
        for forbidden in ('correct_answer', 'explanation', 'is_correct', 'score'):
            self.assertNotIn(forbidden, response.content.decode('utf-8'))


class StudentQuestionFormalEntryTests(StudentApiFixtureMixin, TestCase):
    def test_legacy_capture_v1_entry_is_rejected_without_acquiring_a_lease_or_recording_a_visit(self):
        from .services import get_or_create_session
        with patch('apps.assessment.prepared_services.prepare_participant', return_value=None), \
             override_settings(CAPTURE_V2_ENABLED=False):
            experiment, task = self.publish_task(name='历史capture-v1任务', feedback_mode=self.feedback_mode)
            session = get_or_create_session(experiment, self.student, class_task=task)
        self.client.force_login(self.student)
        assignment = session.assignments.get()
        now = timezone.now().isoformat()
        response = self.post_entry(f'/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/', {
            'reason': 'resume', 'navigation_id': str(uuid.uuid4()), 'client_instance_id': str(uuid.uuid4()),
            'client_occurred_at': now, 'client_time_origin': now, 'client_monotonic_ms': 0,
        })
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['error']['code'], 'WHOLE_PAPER_UPGRADE_REQUIRED')
        self.assertFalse(QuestionVisitEvent.objects.filter(session=session).exists())
        self.assertIsNone(Attempt.objects.get(assignment=assignment).writer_lease_token)

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}))
    def test_entry_returns_client_bound_lease_and_replay_never_resurrects_it(self):
        session, assignment, url, document = self.entry()
        client_id = str(uuid.uuid4())
        document['client_instance_id'] = client_id
        first = self.post_entry(url, document)
        self.assertEqual(first.status_code, 200, first.content)
        lease = first.json()['data']['initial_writer_lease']
        self.assertEqual(lease['writer_client_instance_id'], client_id)
        replay = self.post_entry(url, document)
        self.assertEqual(replay.json()['data']['initial_writer_lease'], lease)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 2)
        for change in (
            {'writer_lease_expires_at': timezone.now() - timedelta(seconds=1)},
            {'writer_lease_token': None, 'writer_client_instance_id': None, 'writer_lease_expires_at': None},
            {'writer_lease_token': uuid.uuid4(), 'writer_client_instance_id': uuid.uuid4(),
             'writer_lease_expires_at': timezone.now() + timedelta(seconds=30)},
        ):
            with self.subTest(change=change):
                Attempt.objects.filter(assignment=assignment).update(**change)
                before = Attempt.objects.filter(assignment=assignment).values().get()
                replay = self.post_entry(url, document)
                self.assertEqual(replay.status_code, 200, replay.content)
                self.assertNotIn('initial_writer_lease', replay.json()['data'])
                self.assertEqual(Attempt.objects.filter(assignment=assignment).values().get(), before)
                self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 2)

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}))
    def test_entry_client_identity_conflict_and_other_client_lease_are_rejected(self):
        session, assignment, url, document = self.entry()
        document['client_instance_id'] = str(uuid.uuid4())
        self.assertEqual(self.post_entry(url, document).status_code, 200)
        changed = {**document, 'client_instance_id': str(uuid.uuid4())}
        conflict = self.post_entry(url, changed)
        self.assertEqual(conflict.status_code, 409, conflict.content)
        conflict = self.post_entry(url, {**changed, 'navigation_id': str(uuid.uuid4())})
        self.assertEqual(conflict.status_code, 409, conflict.content)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 2)

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}))
    def test_submitted_and_ended_entry_replay_never_returns_write_credentials(self):
        session, assignment, url, document = self.entry()
        document['client_instance_id'] = str(uuid.uuid4())
        self.assertEqual(self.post_entry(url, document).status_code, 200)
        attempt = self.submit_attempt_without_finishing(session)
        review = self.post_entry(url, {**document, 'navigation_id': str(uuid.uuid4())})
        self.assertEqual(review.status_code, 200, review.content)
        self.assertNotIn('initial_writer_lease', review.json()['data'])
        for status in (ExperimentSession.Status.IN_PROGRESS, ExperimentSession.Status.COMPLETED):
            ExperimentSession.objects.filter(pk=session.pk).update(status=status,
                terminal_at=timezone.now() if status == ExperimentSession.Status.COMPLETED else None)
            response = self.post_entry(url, document)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertNotIn('initial_writer_lease', response.json()['data'])
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.SUBMITTED)

    def entry(self, *, task=None):
        started = self.start(task=task)
        self.assertEqual(started.status_code, 201, started.content)
        session = ExperimentSession.objects.get(class_task=task or self.task, student=self.student)
        assignment = session.assignments.order_by('assigned_sequence_index').first()
        from .writer_lease_services import release_attempt_writer_lease
        initial = domain_payload(started)['runtime']['initial_writer_lease']
        release_attempt_writer_lease(assignment.attempt, lease_token=initial['writer_lease_token'],
            client_instance_id=initial['writer_client_instance_id'])
        clicked_at = timezone.now() - timedelta(seconds=1)
        document = {'reason': 'navigation_click', 'navigation_id': str(uuid.uuid4()),
                    'client_occurred_at': clicked_at.isoformat(), 'client_monotonic_ms': '20000.500',
                    'client_time_origin': (clicked_at - timedelta(seconds=20)).isoformat()}
        return session, assignment, f'/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/', document

    def post_entry(self, url, document):
        return self.client.post(url, data=document, content_type='application/json')

    def test_submitted_question_can_be_reviewed_and_replayed_without_reopening_answer(self):
        session, assignment, url, document = self.entry()
        first = self.post_entry(url, document)
        self.assertEqual(first.status_code, 200, first.content)
        attempt = self.submit_attempt_without_finishing(session)
        original = (attempt.final_answer, attempt.submitted_at, attempt.last_opened_at)
        for response in (
            self.client.get(url),
            self.post_entry(url, document),
            self.post_entry(url, {**document, 'navigation_id': str(uuid.uuid4())}),
        ):
            self.assertEqual(response.status_code, 200, response.content)
            payload = response.json()['data']
            self.assertTrue(payload['entry_allowed'], payload)
            self.assertEqual(payload['entry_denial_code'], '')
            self.assertEqual(payload['assignment']['status'], 'submitted')
        attempt.refresh_from_db()
        self.assertEqual((attempt.final_answer, attempt.submitted_at, attempt.last_opened_at), original)
        draft = self.client.post(f'/api/v1/student/attempts/{attempt.pk}/draft/',
                                 data={'answer': 'A'}, content_type='application/json')
        self.assertEqual(draft.status_code, 409, draft.content)
        attempt.refresh_from_db()
        self.assertEqual(attempt.final_answer, original[0])

    def test_submitted_question_review_still_obeys_session_deadline(self):
        session, assignment, url, document = self.entry()
        self.post_entry(url, document)
        self.submit_attempt_without_finishing(session)
        ExperimentSession.objects.filter(pk=session.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        for response in (self.client.get(url), self.post_entry(url, document)):
            self.assertEqual(response.status_code, 200, response.content)
            self.assertFalse(response.json()['data']['entry_allowed'])
            self.assertEqual(response.json()['data']['entry_denial_code'], 'DEADLINE_EXPIRED')

    def test_review_uses_session_time_instead_of_the_submitted_item_deadline(self):
        session, assignment, url, document = self.entry()
        self.post_entry(url, document)
        attempt = self.submit_attempt_without_finishing(session)
        ExperimentSession.objects.filter(pk=session.pk).update(deadline_at=timezone.now() + timedelta(minutes=5))
        Attempt.objects.filter(pk=attempt.pk).update(current_effective_deadline_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['data']['entry_allowed'])
        self.assertGreater(response.json()['data']['remaining_seconds'], 290)

    def test_same_navigation_token_and_material_replays_stable_receipt_once(self):
        session, assignment, url, document = self.entry()
        first = self.post_entry(url, document)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertNotIn('initial_writer_lease', first.json()['data'])
        first_receipt = first.json()['data']['entry_receipt']
        self.assertEqual(set(first_receipt), {'navigation_id', 'session_id', 'assignment_id', 'attempt_id',
                                             'visit_id', 'visit_index', 'entry_event_id', 'entered_at'})
        before_attempt = Attempt.objects.filter(assignment=assignment).values().get()
        before_session = ExperimentSession.objects.filter(pk=session.pk).values().get()
        replay = self.post_entry(url, document)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json()['data']['entry_receipt'], first_receipt)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 2)
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), 2)
        self.assertEqual(Attempt.objects.filter(assignment=assignment).values().get(), before_attempt)
        self.assertEqual(ExperimentSession.objects.filter(pk=session.pk).values().get(), before_session)

    def test_same_token_with_changed_reason_or_clock_material_is_rejected(self):
        session, assignment, url, document = self.entry()
        first = self.post_entry(url, document)
        self.assertEqual(first.status_code, 200, first.content)
        before_attempt = Attempt.objects.filter(assignment=assignment).values().get()
        before_session = ExperimentSession.objects.filter(pk=session.pk).values().get()
        changes = {'reason': 'resume', 'client_monotonic_ms': '20001.500',
                   'client_occurred_at': (timezone.now() - timedelta(seconds=2)).isoformat(),
                   'client_time_origin': (timezone.now() - timedelta(seconds=22)).isoformat()}
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = self.post_entry(url, {**document, field: value})
                self.assertEqual(changed.status_code, 409, changed.content)
                self.assertEqual(changed.json()['error']['code'], 'ENTRY_MATERIAL_CONFLICT')
                self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 2)
                self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), 2)
                self.assertEqual(Attempt.objects.filter(assignment=assignment).values().get(), before_attempt)
                self.assertEqual(ExperimentSession.objects.filter(pk=session.pk).values().get(), before_session)

    def test_same_token_for_another_assignment_is_rejected_without_entry(self):
        second = Question.objects.create(bank=self.bank, sequence=2, code='STU-Q2',
            question_type=Question.Type.SINGLE, text='另一题', correct_answer='B')
        QuestionOption.objects.create(question=second, key='A', text='3', display_order=1)
        QuestionOption.objects.create(question=second, key='B', text='4', display_order=2)
        QuestionConcept.objects.create(question=second, concept=self.question.primary_concept, is_primary=True)
        _, task = self.publish_task(name='两个题目的进入任务', feedback_mode=self.feedback_mode)
        session, assignment, url, document = self.entry(task=task)
        first = self.post_entry(url, document)
        self.assertEqual(first.status_code, 200, first.content)
        other = session.assignments.exclude(pk=assignment.pk).get()
        other_url = f'/api/v1/student/sessions/{session.pk}/questions/{other.pk}/'
        conflict = self.post_entry(other_url, document)
        self.assertEqual(conflict.status_code, 409, conflict.content)
        self.assertEqual(conflict.json()['error']['code'], 'ENTRY_MATERIAL_CONFLICT')
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 2)
        self.assertEqual(Attempt.objects.get(assignment=other).status, Attempt.Status.UNVISITED)

    def test_formal_entry_requires_navigation_id_and_complete_clock_group(self):
        session, assignment, url, document = self.entry()
        for field in ('navigation_id', 'client_occurred_at', 'client_monotonic_ms', 'client_time_origin'):
            with self.subTest(missing=field):
                incomplete = {key: value for key, value in document.items() if key != field}
                response = self.post_entry(url, incomplete)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 1)
                self.assertEqual(Attempt.objects.get(assignment=assignment).status, Attempt.Status.VISITED)

    def test_new_formal_entry_after_session_deadline_is_rejected_without_adding_a_visit(self):
        session, assignment, url, document = self.entry()
        ExperimentSession.objects.filter(pk=session.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        response = self.post_entry(url, document)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), 1)

    def test_expired_item_can_be_reviewed_without_reopening_answer_writes(self):
        session, assignment, url, document = self.entry()
        first = self.post_entry(url, document)
        self.assertEqual(first.status_code, 200, first.content)
        receipt = first.json()['data']['entry_receipt']
        Attempt.objects.filter(assignment=assignment).update(
            current_effective_deadline_at=timezone.now() - timedelta(seconds=1))
        original = Attempt.objects.get(assignment=assignment)
        for response in (self.client.get(url), self.post_entry(url, document)):
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.json()['data']['entry_allowed'])
            self.assertFalse(response.json()['data']['response_write_allowed'])
        replay = self.post_entry(url, document)
        self.assertEqual(replay.json()['data']['entry_receipt'], receipt)
        reviewed = self.post_entry(url, {**document, 'navigation_id': str(uuid.uuid4())})
        self.assertEqual(reviewed.status_code, 200, reviewed.content)
        self.assertFalse(reviewed.json()['data']['response_write_allowed'])
        self.assertNotIn('initial_writer_lease', reviewed.json()['data'])
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 3)
        current = Attempt.objects.get(assignment=assignment)
        self.assertEqual(current.active_response_event_id, original.active_response_event_id)
        self.assertEqual(current.draft_answer, original.draft_answer)
        self.assertEqual(current.response_version, original.response_version)
        self.assertIn(current.status, (Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED))

    def test_successful_entry_replay_after_deadline_confirms_receipt_with_read_only_dto(self):
        session, assignment, url, document = self.entry()
        first = self.post_entry(url, document)
        self.assertEqual(first.status_code, 200, first.content)
        receipt = first.json()['data']['entry_receipt']
        ExperimentSession.objects.filter(pk=session.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        before_session = ExperimentSession.objects.filter(pk=session.pk).values().get()
        replay = self.post_entry(url, document)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json()['data']['entry_receipt'], receipt)
        self.assertFalse(replay.json()['data']['entry_allowed'])
        self.assertEqual(replay.json()['data']['entry_denial_code'], 'DEADLINE_EXPIRED')
        self.assertEqual(ExperimentSession.objects.filter(pk=session.pk).values().get(), before_session)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 2)

    def test_successful_token_cannot_replay_question_after_enrollment_revocation(self):
        from apps.accounts.models import CourseEnrollment

        session, assignment, url, document = self.entry()
        first = self.post_entry(url, document)
        self.assertEqual(first.status_code, 200, first.content)
        enrollment = CourseEnrollment.objects.get(student=self.student, course=self.course)
        enrollment.is_active = False
        enrollment.save(update_fields=['is_active'])
        response = self.post_entry(url, document)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertNotIn('question', response.json())
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 2)
