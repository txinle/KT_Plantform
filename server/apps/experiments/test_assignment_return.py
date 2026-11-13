from unittest.mock import patch
from queue import Queue
from threading import Barrier, Thread

from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.api.test_support import domain_payload
from apps.assessment.models import ExperimentSession, SubmissionEvent, GradeRecord, AssessmentEvent
from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.audit.models import AuditLog
from apps.analytics.models import StudentExperimentResultSnapshot
from .publication_services import manual_close_task


class AssignmentReturnTests(StudentApiFixtureMixin, TestCase):
    prepared_paper_fixture = True

    def setUp(self):
        super().setUp()
        response = self.start()
        self.assertEqual(response.status_code, 201, response.content)
        self.session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.attempt = self.submit_attempt_without_finishing(self.session)
        ExperimentSession.objects.filter(pk=self.session.pk).update(submission_count=1)
        self.participant = self.session.participant_snapshot
        self.url = f'/api/v1/teacher/class-tasks/{self.task.pk}/participants/{self.participant.pk}/return/'
        self.client.force_login(self.teacher)

    def preview(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        return domain_payload(response)

    def execute(self, preview):
        return self.client.post(self.url, {'confirmation_token': preview['confirmation_token']}, content_type='application/json')

    def test_returns_only_selected_student_preserving_frozen_paper_and_schedule(self):
        old_order = list(self.session.assignments.order_by('assigned_sequence_index').values_list('snapshot_id', flat=True))
        peer = ExperimentSession.objects.get(class_task=self.task, student=self.peer)
        schedule_ids = list(self.task.schedule_revisions.values_list('pk', flat=True))
        StudentExperimentResultSnapshot.objects.create(class_task=self.task, student=self.student,
            source_signature='old', payload={'answer': 'B'}, generated_at=timezone.now())
        preview = self.preview()
        self.assertTrue(preview['can_return'])
        self.assertEqual(preview['student_number'], self.student.student_number)
        response = self.execute(preview)
        self.assertEqual(response.status_code, 200, response.content)
        new = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.assertNotEqual(new.pk, self.session.pk)
        self.assertEqual((new.status, new.submission_count, new.started_at, new.deadline_at), ('prepared', 0, None, None))
        self.assertEqual(list(new.assignments.order_by('assigned_sequence_index').values_list('snapshot_id', flat=True)), old_order)
        self.assertEqual(list(self.task.schedule_revisions.values_list('pk', flat=True)), schedule_ids)
        self.assertTrue(ExperimentSession.objects.filter(pk=peer.pk).exists())
        self.assertFalse(SubmissionEvent.objects.filter(attempt_id=self.attempt.pk).exists())
        self.assertFalse(GradeRecord.objects.filter(submission__attempt_id=self.attempt.pk).exists())
        self.assertFalse(AssessmentEvent.objects.filter(session_id=self.session.pk).exists())
        self.assertFalse(StudentExperimentResultSnapshot.objects.filter(class_task=self.task, student=self.student).exists())
        self.assertTrue(AuditLog.objects.filter(action='experiment.student_session_reset', actor=self.teacher).exists())
        self.assertEqual(self.execute(preview).status_code, 200)
        self.assertEqual(ExperimentSession.objects.get(class_task=self.task, student=self.student).pk, new.pk)
        self.assertEqual(AuditLog.objects.filter(action='experiment.student_session_reset').count(), 1)
        self.client.force_login(self.student)
        old = self.client.post(f'/api/v1/student/attempts/{self.attempt.pk}/draft/', {'answer': 'A'}, content_type='application/json')
        self.assertEqual(old.status_code, 404)
        self.assertEqual(self.start().status_code, 201)

    def test_changed_answer_requires_fresh_confirmation(self):
        preview = self.preview()
        ExperimentSession.objects.filter(pk=self.session.pk).update(research_source_revision=99)
        response = self.execute(preview)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())

    def test_wrong_student_token_and_tampered_token_cannot_clear_records(self):
        preview = self.preview()
        peer = self.task.participant_snapshots.get(student=self.peer)
        response = self.client.post(self.url.replace(f'/{self.participant.pk}/return/', f'/{peer.pk}/return/'),
            {'confirmation_token': preview['confirmation_token']}, content_type='application/json')
        self.assertEqual(response.status_code, 409, response.content)
        preview['confirmation_token'] += 'tampered'
        self.assertEqual(self.execute(preview).status_code, 409)
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())

    def test_student_and_unrelated_teacher_cannot_return_assignment(self):
        preview = self.preview()
        outsider = User.objects.create_user(username='unrelated-teacher', role=User.Role.TEACHER)
        for actor in (self.student, outsider):
            self.client.force_login(actor)
            self.assertIn(self.client.get(self.url).status_code, (403, 404))
            self.assertIn(self.execute(preview).status_code, (403, 404))
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())

    def test_closed_task_cannot_be_returned_or_silently_reopened(self):
        preview = self.preview()
        manual_close_task(self.task, self.teacher, reason='已结束')
        response = self.execute(preview)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(self.preview()['can_return'])
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())

    def test_unstarted_student_has_no_clear_action(self):
        peer = self.task.participant_snapshots.get(student=self.peer)
        self.url = self.url.replace(f'/{self.participant.pk}/return/', f'/{peer.pk}/return/')
        preview = self.preview()
        self.assertFalse(preview['can_return'])
        self.assertIsNone(preview['confirmation_token'])

    def test_rebuild_failure_rolls_back_deletion(self):
        preview = self.preview()
        with patch('apps.experiments.assignment_return.prepare_participant', side_effect=RuntimeError('rebuild failed')):
            with self.assertRaisesMessage(RuntimeError, 'rebuild failed'):
                self.execute(preview)
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(SubmissionEvent.objects.filter(attempt_id=self.attempt.pk).exists())

    def test_coverage_references_are_not_removed_with_other_students_results(self):
        preview = self.preview()
        with patch('apps.experiments.assignment_return._coverage_runs', return_value=[123]):
            self.assertFalse(self.preview()['can_return'])
            self.assertEqual(self.execute(preview).status_code, 409)
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())


class AssignmentReturnConcurrencyTests(StudentApiFixtureMixin, TransactionTestCase):
    prepared_paper_fixture = True

    def test_two_confirmations_create_one_replacement_session(self):
        response = self.start()
        self.assertEqual(response.status_code, 201, response.content)
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        url = f'/api/v1/teacher/class-tasks/{self.task.pk}/participants/{session.participant_snapshot_id}/return/'
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(self.teacher)
        preview = domain_payload(clients[0].get(url))
        barrier, results = Barrier(2), Queue()

        def send(client):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = client.post(url, {'confirmation_token': preview['confirmation_token']}, content_type='application/json')
                results.put((response.status_code, response.json()))
            except Exception as exc:
                results.put(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=send, args=(client,), daemon=True) for client in clients]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
        responses = [results.get_nowait(), results.get_nowait()]
        self.assertTrue(all(isinstance(row, tuple) and row[0] == 200 for row in responses), responses)
        self.assertEqual(responses[0][1]['data'], responses[1][1]['data'])
        self.assertEqual(ExperimentSession.objects.filter(participant_snapshot_id=session.participant_snapshot_id).count(), 1)
        self.assertFalse(ExperimentSession.objects.filter(pk=session.pk).exists())
        self.assertEqual(AuditLog.objects.filter(action='experiment.student_session_reset').count(), 1)
