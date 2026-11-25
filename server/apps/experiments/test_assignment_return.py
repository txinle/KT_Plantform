import csv
import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
from queue import Queue
from threading import Barrier, Thread

from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.api.test_support import domain_payload
from apps.assessment.models import ExperimentSession, SubmissionEvent, GradeRecord, AssessmentEvent
from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.audit.models import AuditLog
from apps.analytics.models import StudentExperimentResultSnapshot, RecordingCoverageRun, ExportSnapshot, ExportJob
from apps.analytics.recording_coverage import generate_scope_coverage
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

    def test_started_student_with_zero_submissions_can_be_returned(self):
        self.assertEqual(self.start(student=self.peer).status_code, 201)
        participant = self.task.participant_snapshots.get(student=self.peer)
        self.url = self.url.replace(f'/{self.participant.pk}/return/', f'/{participant.pk}/return/')
        self.client.force_login(self.teacher)
        preview = self.preview()
        self.assertEqual(preview['submission_count'], 0)
        response = self.execute(preview)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(self.preview()['can_return'])
        self.assertTrue(ExperimentSession.objects.filter(
            participant_snapshot=participant, status=ExperimentSession.Status.PREPARED).exists())

    def test_large_failed_history_is_drained_before_the_single_atomic_reset(self):
        from apps.analytics.models import ResearchSessionMaterialization as Materialization

        history = Materialization.objects.bulk_create([
            Materialization(session=self.session, status='failed') for _ in range(300)])
        token = self.preview()['confirmation_token']
        def send(token):
            return self.client.post(self.url, {'confirmation_token': token, 'allow_batches': True},
                                    content_type='application/json')
        first = send(token)
        self.assertEqual(first.status_code, 202, first.content)
        self.assertTrue(SubmissionEvent.objects.filter(attempt=self.attempt).exists())
        self.assertFalse(AuditLog.objects.filter(action='experiment.student_session_reset').exists())
        response = first
        for _ in range(len(history)):
            if response.status_code != 202:
                break
            result = domain_payload(response)
            self.assertGreater(result['processed'], 0)
            response = send(result['confirmation_token'])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(domain_payload(response)['status'], 'not_started')
        self.assertEqual(domain_payload(send(token)), domain_payload(response))
        self.assertFalse(Materialization.objects.filter(session_id=self.session.pk).exists())

    def test_rebuild_failure_rolls_back_deletion(self):
        preview = self.preview()
        with patch('apps.experiments.assignment_return.prepare_participant', side_effect=RuntimeError('rebuild failed')):
            with self.assertRaisesMessage(RuntimeError, 'rebuild failed'):
                self.execute(preview)
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(SubmissionEvent.objects.filter(attempt_id=self.attempt.pk).exists())

    def coverage_export(self):
        self.assertEqual(self.start(student=self.peer).status_code, 201)
        peer = ExperimentSession.objects.get(class_task=self.task, student=self.peer)
        peer_attempt = self.submit_attempt_without_finishing(peer)
        run = generate_scope_coverage([self.session, peer])
        peer_run = generate_scope_coverage([peer])
        snapshot = ExportSnapshot.objects.create(course=self.course, experiment=self.task.experiment,
            created_by=self.teacher, coverage_run=run)
        job = ExportJob.objects.create(course=self.course, snapshot=snapshot, created_by=self.teacher,
            status=ExportJob.Status.SUCCEEDED)
        self.client.force_login(self.teacher)
        return run, peer_run, peer, peer_attempt, snapshot, job

    def test_return_preserves_frozen_export_and_peer_coverage(self):
        run, peer_run, peer, peer_attempt, _, job = self.coverage_export()
        preview = self.preview()
        self.assertTrue(preview['can_return'])
        with CaptureQueriesContext(connection) as queries:
            response = self.execute(preview)
        self.assertEqual(response.status_code, 200, response.content)
        for table in (ExportJob, ExportSnapshot):
            self.assertFalse(any(table._meta.db_table in q['sql'] for q in queries))
        self.assertFalse(ExperimentSession.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(ExportJob.objects.filter(pk=job.pk, snapshot__coverage_run=run,
                                                 status=ExportJob.Status.SUCCEEDED).exists())
        self.assertFalse(run.session_results.filter(session_id=self.session.pk).exists())
        self.assertFalse(run.attempt_results.filter(attempt_id=self.attempt.pk).exists())
        self.assertTrue(run.session_results.filter(session=peer).exists())
        self.assertTrue(run.attempt_results.filter(attempt=peer_attempt).exists())
        self.assertTrue(RecordingCoverageRun.objects.filter(pk=peer_run.pk).exists())
        self.assertTrue(ExperimentSession.objects.filter(pk=peer.pk).exists())
        self.assertTrue(SubmissionEvent.objects.filter(attempt=peer_attempt).exists())
        self.client.force_login(self.student)
        self.assertEqual(self.start().status_code, 201)

    def test_failed_return_restores_coverage_and_export(self):
        run, _, _, _, snapshot, job = self.coverage_export()
        preview = self.preview()
        self.assertTrue(preview['can_return'])
        with patch('apps.experiments.assignment_return.prepare_participant', side_effect=RuntimeError('rebuild failed')):
            with self.assertRaisesMessage(RuntimeError, 'rebuild failed'):
                self.execute(preview)
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(RecordingCoverageRun.objects.filter(pk=run.pk).exists())
        self.assertTrue(ExportSnapshot.objects.filter(pk=snapshot.pk).exists())
        self.assertTrue(ExportJob.objects.filter(pk=job.pk).exists())

    def test_completed_export_download_survives_and_new_export_reads_reset_state(self):
        from apps.analytics.research_materialization import (
            claim_session_materialization, enqueue_session_materialization, run_session_materialization,
        )
        from apps.analytics.services import claim_export_job, run_export_job

        finished_at = timezone.now()
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            status=ExperimentSession.Status.COMPLETED, completed_at=finished_at, terminal_at=finished_at)

        def materialize(session):
            enqueue_session_materialization(session)
            item = claim_session_materialization(worker_id='return-test')
            result = run_session_materialization(item, worker_id='return-test')
            self.assertEqual(result.status, result.Status.SUCCEEDED, result.error)

        materialize(self.session)
        endpoint = f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/'
        payload = {'package': 'experiment', 'confirm_identified': True,
                   'scope': {'scope_version': 2, 'experiment_id': self.task.experiment_id, 'class_mode': 'all'}}

        def export():
            created = self.client.post(endpoint, payload, content_type='application/json')
            self.assertEqual(created.status_code, 201, created.content)
            job = run_export_job(claim_export_job(worker_id='return-test'))
            return domain_payload(self.client.get(f'{endpoint}{job.pk}/'))['download_url']

        def download(url):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            return b''.join(response.streaming_content)

        def response_rows(content):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                table = archive.read('05_单次实验_逐题作答表.csv').decode('utf-8-sig')
                return list(csv.DictReader(io.StringIO(table)))

        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=Path(media)):
            original_url = export()
            original = download(original_url)
            original_rows = response_rows(original)
            self.assertIn('B', {row['最终答案'] for row in original_rows})
            returned = self.execute(self.preview())
            self.assertEqual(returned.status_code, 200, returned.content)
            self.assertEqual(download(original_url), original)
            self.assertEqual(response_rows(download(export())), [])
            self.assertEqual(self.start().status_code, 201)
            replacement = ExperimentSession.objects.get(class_task=self.task, student=self.student)
            self.complete_session(replacement, answer='A')
            materialize(replacement)
            self.client.force_login(self.teacher)
            rows = response_rows(download(export()))
            self.assertEqual({row['最终答案'] for row in rows}, {'A'})
            self.assertTrue({row['sessionID'] for row in rows}.isdisjoint(
                row['sessionID'] for row in original_rows))


class AssignmentReturnConcurrencyTests(StudentApiFixtureMixin, TransactionTestCase):
    prepared_paper_fixture = True

    def test_expired_session_with_production_scale_failed_history(self):
        from apps.analytics.models import ResearchSessionMaterialization as Materialization

        self.assertEqual(self.start().status_code, 201)
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        ExperimentSession.objects.filter(pk=session.pk).update(status='expired', terminal_at=timezone.now())
        Materialization.objects.bulk_create(
            (Materialization(session=session, status='failed') for _ in range(22530)), batch_size=500)
        Materialization.objects.bulk_create([
            Materialization(session=session, status='succeeded', is_current=i == 2) for i in range(3)])
        url = f'/api/v1/teacher/class-tasks/{self.task.pk}/participants/{session.participant_snapshot_id}/return/'
        self.client.force_login(self.teacher)
        preview = domain_payload(self.client.get(url))
        self.assertEqual(preview['submission_count'], 0)
        token = preview['confirmation_token']
        for _ in range(180):
            response = self.client.post(url, {'confirmation_token': token, 'allow_batches': True},
                                        content_type='application/json')
            if response.status_code != 202:
                break
            token = domain_payload(response)['confirmation_token']
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(domain_payload(response)['status'], 'not_started')
        self.assertFalse(Materialization.objects.filter(session=session).exists())
        self.assertEqual(AuditLog.objects.filter(action='experiment.student_session_reset').count(), 1)

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
