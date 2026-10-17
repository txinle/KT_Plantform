"""Public student entry always selects the whole-paper workflow."""
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.audit.models import AuditLog
from .models import AssessmentEvent, ExperimentSession, GradeRecord, QuestionVisitEvent, SubmissionEvent
from .prepared_models import PreparedAttemptProjection, PreparedSessionArtifact
from .test_student_api import StudentApiFixtureMixin


@override_settings(ASSESSMENT_PREPARED_ENABLED=False,
                   ASSESSMENT_ADMISSION_ENABLED=True,
                   ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True,
                   ASSESSMENT_PREPARED_ADMISSION_MODE='postgres_transaction')
class CanonicalPaperTests(StudentApiFixtureMixin, TestCase):
    prepared_paper_fixture = True
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        configuration = override_settings(ASSESSMENT_PAPER_ROOT=Path(directory.name))
        configuration.enable()
        self.addCleanup(configuration.disable)
        super().setUp()

    def test_publication_prepares_papers_even_when_obsolete_flag_is_false(self):
        self.assertEqual(PreparedSessionArtifact.objects.filter(session__class_task=self.task, ready=True).count(), 2)

    def test_start_cannot_select_the_old_protocol_when_obsolete_flag_is_false(self):
        self.client.force_login(self.student)
        response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/start/',
            {'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4())},
            content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.assertEqual(session.assessment_flow_version, 1)
        runtime = response.json().get('data', response.json())['runtime']
        self.assertNotIn('submit_url', runtime['actions'])
        self.assertNotIn('skip_url', runtime['actions'])

    def historical_session(self):
        from .prepared_services import activate_prepared_session, prepare_participant
        # This fixture has real frozen assignments and a real captured visit.
        # Remove only derived paper products to model an already-started v2 paper.
        prepare_participant(self.task.participant_snapshots.get(student=self.student))
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        PreparedAttemptProjection.objects.filter(attempt__session=session).delete()
        PreparedSessionArtifact.objects.filter(session=session).delete()
        ExperimentSession.objects.filter(pk=session.pk).update(assessment_flow_version=0)
        session.refresh_from_db()
        return session

    def test_upgrade_preserves_frozen_work_and_is_idempotent(self):
        from .prepared_services import ensure_canonical_paper_session
        session = self.historical_session()
        attempt = session.attempts.get()
        attempt.draft_answer = 'B'
        attempt.response_version = 7
        attempt.save(update_fields=['draft_answer', 'response_version'])
        before_session = (session.started_at, session.deadline_at, session.capture_contract_id,
                          session.transition_protocol_version, session.current_assignment_id)
        before_events = list(AssessmentEvent.objects.filter(session=session).values())
        before_visits = list(QuestionVisitEvent.objects.filter(session=session).values())
        ids = list(session.assignments.values_list('pk', 'snapshot_id', 'item_instance_id'))
        upgraded = ensure_canonical_paper_session(session)
        ensure_canonical_paper_session(upgraded)
        session.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(session.assessment_flow_version, 1)
        self.assertEqual((session.started_at, session.deadline_at, session.capture_contract_id,
                          session.transition_protocol_version, session.current_assignment_id), before_session)
        self.assertEqual(list(session.assignments.values_list('pk', 'snapshot_id', 'item_instance_id')), ids)
        self.assertEqual(list(AssessmentEvent.objects.filter(session=session).values()), before_events)
        self.assertEqual(list(QuestionVisitEvent.objects.filter(session=session).values()), before_visits)
        self.assertEqual((attempt.draft_answer, attempt.response_version), ('B', 7))
        self.assertEqual(session.prepared_artifact.item_count, 1)
        self.assertEqual(AuditLog.objects.filter(action='assessment.whole_paper_upgraded', object_id=str(session.pk)).count(), 1)

    def test_direct_runtime_entry_converts_before_recording_another_visit(self):
        session = self.historical_session()
        attempt = session.attempts.get()
        before = QuestionVisitEvent.objects.filter(session=session).count()
        self.client.force_login(self.student)
        response = self.client.get(f'/api/v1/student/sessions/{session.pk}/questions/{attempt.assignment_id}/')
        self.assertEqual(response.status_code, 200, response.content)
        runtime = response.json().get('data', response.json())
        self.assertEqual(runtime['assessment_flow_version'], 1)
        self.assertNotIn('submit_url', runtime['actions'])
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), before)

    def test_repeated_start_of_upgraded_work_resumes_without_reactivation(self):
        session = self.historical_session()
        self.client.force_login(self.student)
        for _ in range(2):
            response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/start/',
                {'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4())},
                content_type='application/json')
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json().get('data', response.json())['session_id'], session.pk)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 1)

    def test_upgrade_preserves_already_submitted_items_and_grades(self):
        from .prepared_services import ensure_canonical_paper_session, prepared_runtime
        session = self.historical_session()
        attempt = self.submit_attempt_without_finishing(session)
        before_submissions = list(SubmissionEvent.objects.filter(attempt=attempt).values())
        before_grades = list(GradeRecord.objects.filter(submission__attempt=attempt).values())
        session = ensure_canonical_paper_session(session)
        attempt.refresh_from_db()
        attempt.session = session
        runtime = prepared_runtime(attempt)
        self.assertFalse(runtime['response_write_allowed'])
        self.assertEqual(attempt.status, 'submitted')
        self.assertEqual(list(SubmissionEvent.objects.filter(attempt=attempt).values()), before_submissions)
        self.assertEqual(list(GradeRecord.objects.filter(submission__attempt=attempt).values()), before_grades)

    def test_unsupported_immutable_capture_is_rejected_before_entry_writes(self):
        session = self.historical_session()
        ExperimentSession.objects.filter(pk=session.pk).update(transition_protocol_version=0)
        session.refresh_from_db()
        before = list(AssessmentEvent.objects.filter(session=session).values_list('pk', flat=True))
        self.client.force_login(self.student)
        response = self.client.get(f'/api/v1/student/sessions/{session.pk}/questions/{session.assignments.get().pk}/')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['error']['code'], 'WHOLE_PAPER_UPGRADE_REQUIRED')
        session.refresh_from_db()
        self.assertEqual(session.assessment_flow_version, 0)
        self.assertEqual(list(AssessmentEvent.objects.filter(session=session).values_list('pk', flat=True)), before)

    def test_completed_historical_sessions_are_not_rewritten(self):
        from .prepared_services import ensure_canonical_paper_session
        session = self.historical_session()
        self.complete_session(session)
        with self.assertRaisesMessage(ValidationError, 'WHOLE_PAPER_UPGRADE_REQUIRED'):
            ensure_canonical_paper_session(session)
        session.refresh_from_db()
        self.assertEqual(session.assessment_flow_version, 0)

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}),
                       ASSESSMENT_TRANSITION_ENABLED=True, ASSESSMENT_TRANSITION_USER_IDS=frozenset({'*'}))
    def test_upgraded_session_first_visit_never_reenters_retired_permit_protocol(self):
        from .services import get_or_create_session
        from django.utils import timezone
        with patch('apps.assessment.prepared_services.prepare_participant', return_value=None):
            experiment, task = self.publish_task(name='历史尚未访题任务', feedback_mode=self.feedback_mode)
        session = get_or_create_session(experiment, self.student, class_task=task)
        self.assertFalse(QuestionVisitEvent.objects.filter(session=session).exists())
        now = timezone.now().isoformat()
        self.client.force_login(self.student)
        with patch('apps.assessment.api.views.AdmissionCoordinator') as coordinator:
            coordinator.enabled_for.return_value = True
            response = self.client.post(
                f'/api/v1/student/sessions/{session.pk}/questions/{session.assignments.get().pk}/',
                {'reason': 'resume', 'navigation_id': str(uuid.uuid4()),
                 'client_instance_id': str(uuid.uuid4()), 'client_occurred_at': now,
                 'client_time_origin': now, 'client_monotonic_ms': 0}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        coordinator.return_value.request.assert_not_called()
        coordinator.return_value.status.assert_not_called()
        coordinator.return_value.consume.assert_not_called()
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 1)
