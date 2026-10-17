"""Question navigation never re-enters the retired activation-permit path."""
import uuid
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .admission import AdmissionResult, AdmissionUnavailable
from .models import AssessmentEvent, ExperimentSession
from .test_student_api import StudentApiFixtureMixin


class AdmissionEntryTests(StudentApiFixtureMixin, TestCase):
    def entry_fixture(self):
        self.assertEqual(self.start().status_code, 201)
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        assignment = session.assignments.get()
        now = timezone.now().isoformat()
        document = {'reason': 'navigation_click', 'navigation_id': str(uuid.uuid4()),
                    'client_occurred_at': now, 'client_monotonic_ms': '0.000',
                    'client_time_origin': now, 'admission_token': 'permit', 'admission_action': 'start'}
        coordinator = MagicMock()
        coordinator.status.return_value = AdmissionResult('permit', 'admitted', 0)
        return session, assignment, document, coordinator

    def post_entry(self, session, assignment_id, document):
        return self.client.post(f'/api/v1/student/sessions/{session.pk}/questions/{assignment_id}/',
                                data=document, content_type='application/json')

    def admission_settings(self):
        return override_settings(ASSESSMENT_ADMISSION_ENABLED=True,
                                 ASSESSMENT_ADMISSION_TASK_IDS=frozenset({str(self.task.pk)}))

    def test_invalid_assignment_does_not_consume_first_entry_permit(self):
        session, assignment, document, coordinator = self.entry_fixture()
        with self.admission_settings(), patch('apps.assessment.api.views.AdmissionCoordinator', return_value=coordinator):
            response = self.post_entry(session, assignment.pk + 99999, document)
        self.assertEqual(response.status_code, 404)
        coordinator.consume.assert_not_called()

    def test_failed_entry_rolls_back_without_consuming_permit(self):
        session, assignment, document, coordinator = self.entry_fixture()
        with self.admission_settings(), patch('apps.assessment.api.views.AdmissionCoordinator', return_value=coordinator), \
                patch('apps.assessment.api.views.record_visit_entry', side_effect=ValidationError('entry failed')):
            response = self.post_entry(session, assignment.pk, document)
        self.assertEqual(response.status_code, 409)
        coordinator.consume.assert_not_called()
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), 1)

    def test_revoked_course_access_does_not_consume_permit(self):
        from apps.accounts.models import CourseEnrollment
        session, assignment, document, coordinator = self.entry_fixture()
        CourseEnrollment.objects.filter(student=self.student, course=self.course).update(is_active=False)
        with self.admission_settings(), patch('apps.assessment.api.views.AdmissionCoordinator', return_value=coordinator):
            response = self.post_entry(session, assignment.pk, document)
        self.assertEqual(response.status_code, 409)
        coordinator.consume.assert_not_called()

    def test_navigation_and_replay_never_consume_an_activation_permit(self):
        session, assignment, document, coordinator = self.entry_fixture()
        with self.admission_settings(), patch('apps.assessment.api.views.AdmissionCoordinator', return_value=coordinator):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.post_entry(session, assignment.pk, document)
                self.assertEqual(response.status_code, 200, response.content)
                coordinator.consume.assert_not_called()
            self.assertFalse(callbacks)
            coordinator.consume.assert_not_called()
            replay = self.post_entry(session, assignment.pk,
                                     {key: value for key, value in document.items() if not key.startswith('admission_')})
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(response.json()['data']['entry_receipt'], replay.json()['data']['entry_receipt'])
        coordinator.consume.assert_not_called()
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), 2)

    def test_retired_queued_permit_has_no_authority_over_paper_navigation(self):
        session, assignment, document, coordinator = self.entry_fixture()
        coordinator.status.return_value = AdmissionResult('permit', 'queued', 3)
        with self.admission_settings(), patch('apps.assessment.api.views.AdmissionCoordinator', return_value=coordinator):
            response = self.post_entry(session, assignment.pk, document)
        self.assertEqual(response.status_code, 200, response.content)
        coordinator.consume.assert_not_called()
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), 2)


class AdmissionEntryCommitTests(StudentApiFixtureMixin, TransactionTestCase):
    entry_fixture = AdmissionEntryTests.entry_fixture
    post_entry = AdmissionEntryTests.post_entry
    admission_settings = AdmissionEntryTests.admission_settings

    def test_navigation_and_replay_do_not_depend_on_the_retired_redis_permit(self):
        session, assignment, document, coordinator = self.entry_fixture()
        coordinator.consume.side_effect = AdmissionUnavailable
        with self.admission_settings(), patch('apps.assessment.api.views.AdmissionCoordinator', return_value=coordinator):
            response = self.post_entry(session, assignment.pk, document)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), 2)
            replay = self.post_entry(session, assignment.pk,
                                     {key: value for key, value in document.items() if not key.startswith('admission_')})
        self.assertEqual(replay.status_code, 200, replay.content)
        coordinator.consume.assert_not_called()
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='item_entered').count(), 2)
