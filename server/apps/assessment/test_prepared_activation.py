"""Publishing must absorb per-item startup work, without starting students."""
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import AssessmentEvent, ConfidenceAssignment, ExperimentSession
from .test_student_api import StudentApiFixtureMixin


class InlineConfidenceContractTests(SimpleTestCase):
    def test_inline_confidence_is_part_of_the_draft_service_not_a_formal_commit(self):
        from . import prepared_services
        self.assertTrue(callable(getattr(prepared_services, 'save_inline_confidence', None)))


@override_settings(ASSESSMENT_PREPARED_ENABLED=True)
class PreparedPublicationTests(StudentApiFixtureMixin, TestCase):
    prepared_paper_fixture = True
    @override_settings(ASSESSMENT_ADMISSION_ENABLED=True, ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
    def test_first_start_activates_in_one_request_and_replay_needs_no_redis(self):
        from .admission import AdmissionResult
        self.client.force_login(self.student)
        document = {'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4())}
        with patch('apps.assessment.api.prepared_start.AdmissionCoordinator') as coordinator:
            coordinator.return_value.request.return_value = AdmissionResult(
                'permit', 'executing', 0, True, str(uuid.uuid4()))
            first = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/start/', document, content_type='application/json')
            self.assertEqual(first.status_code, 201, first.content)
            replay = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/start/', document, content_type='application/json')
            self.assertEqual(replay.status_code, 200, replay.content)
            coordinator.return_value.request.assert_called_once()
            coordinator.return_value.status.assert_not_called()
            coordinator.return_value.claim.assert_not_called()
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        from .models import QuestionVisitEvent
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 1)

    @override_settings(ASSESSMENT_ADMISSION_ENABLED=True, ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
    def test_busy_start_returns_429_without_reservation_or_starting_session(self):
        from .admission import AdmissionResult
        self.client.force_login(self.student)
        with patch('apps.assessment.api.prepared_start.AdmissionCoordinator') as coordinator:
            coordinator.return_value.request.return_value = AdmissionResult('', 'busy', 1)
            response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/start/',
                {'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4())}, content_type='application/json')
        self.assertEqual(response.status_code, 429, response.content)
        self.assertEqual(response.json()['error']['code'], 'START_BUSY')
        self.assertEqual(response.headers['Retry-After'], '1')
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.assertEqual(session.status, 'prepared')
        self.assertIsNone(session.started_at)

    @override_settings(ASSESSMENT_ADMISSION_ENABLED=True, ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True)
    def test_duplicate_executing_start_returns_202_without_poll_url(self):
        from .admission import AdmissionResult
        self.client.force_login(self.student)
        with patch('apps.assessment.api.prepared_start.AdmissionCoordinator') as coordinator:
            coordinator.return_value.request.return_value = AdmissionResult('permit', 'executing', 1, False, str(uuid.uuid4()))
            response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/start/',
                {'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4())}, content_type='application/json')
        self.assertEqual(response.status_code, 202)
        payload = response.json().get('data', response.json())
        self.assertEqual(payload['status'], 'executing')
        self.assertNotIn('poll_url', payload)

    def setUp(self):
        self.paper_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.paper_directory.cleanup)
        self.paper_settings = override_settings(ASSESSMENT_PAPER_ROOT=Path(self.paper_directory.name))
        self.paper_settings.enable()
        self.addCleanup(self.paper_settings.disable)
        super().setUp()

    def test_publish_has_persisted_unstarted_resources_for_every_participant(self):
        sessions = ExperimentSession.objects.filter(class_task=self.task)
        self.assertEqual(sessions.count(), 2)
        self.assertEqual(set(sessions.values_list('status', flat=True)), {'prepared'})
        self.assertFalse(sessions.exclude(started_at=None).exists())
        self.assertFalse(sessions.exclude(deadline_at=None).exists())
        self.assertEqual(ConfidenceAssignment.objects.filter(attempt__session__class_task=self.task).count(), 2)
        self.assertFalse(AssessmentEvent.objects.filter(session__class_task=self.task).exists())

    def test_activation_never_calls_lazy_session_or_full_question_projection(self):
        sessions = ExperimentSession.objects.filter(class_task=self.task, student=self.student)
        self.assertTrue(sessions.exists(), 'publish must prepare the student before activation')
        from .prepared_services import activate_prepared_session
        with patch('apps.assessment.services.get_or_create_session', side_effect=AssertionError('lazy startup')), \
             patch('apps.assessment.api.views.StudentRuntimeQuestionEntryView.payload', side_effect=AssertionError('whole-paper runtime'), create=True), \
             CaptureQueriesContext(connection) as queries:
            session, receipt = activate_prepared_session(
                student=self.student, task_id=self.task.pk,
                client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4(),
                request_received_at=timezone.now(),
            )
        self.assertEqual(session.status, 'in_progress')
        self.assertTrue(receipt['runtime']['entry_allowed'])
        self.assertEqual(receipt['runtime']['question']['text'], '')
        self.assertEqual(receipt['runtime']['navigation'], [])
        self.assertLessEqual(len(queries), 40)
        self.assertFalse(any('INSERT INTO "assessment_attempt"' in q['sql'] for q in queries))
        self.assertFalse(any(q['sql'].startswith('SELECT') and
                             ('"text"' in q['sql'] or '"correct_answer"' in q['sql']) for q in queries))

    def test_prepared_students_remain_not_started_in_teacher_progress(self):
        self.client.force_login(self.teacher)
        response = self.client.get(f'/api/v1/teacher/class-tasks/{self.task.pk}/progress/')
        self.assertEqual(response.status_code, 200)
        payload = response.json().get('data', response.json())
        self.assertEqual(payload['not_started'], 2)
        self.assertEqual(payload['in_progress'], 0)

    def test_prepared_resume_is_rejected_without_reserving_global_capacity(self):
        from .admission import AdmissionResult
        self.client.force_login(self.student)
        with patch('apps.assessment.api.views.AdmissionCoordinator') as coordinator:
            coordinator.global_enabled.return_value = True
            coordinator.enabled_for.return_value = True
            coordinator.return_value.request.return_value = AdmissionResult('permit', 'admitted', 0)
            response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/resume/',
                data={}, content_type='application/json')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['error']['code'], 'SESSION_NOT_STARTED')
        coordinator.return_value.request.assert_not_called()

    def test_started_paper_resume_reuses_frozen_confidence_and_original_start(self):
        from .prepared_services import activate_prepared_session
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        started_at = session.started_at
        self.client.force_login(self.student)
        with patch('apps.assessment.services.materialize_confidence_assignments',
                   side_effect=AssertionError('confidence is already prepared')):
            response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/resume/',
                data={}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        session.refresh_from_db()
        self.assertEqual(session.started_at, started_at)
        self.assertEqual(session.visit_event_count, 1)

    def test_new_schedule_cannot_reopen_an_accepted_whole_paper(self):
        from apps.experiments.models import ScheduleRevision
        from .prepared_services import activate_prepared_session
        from .session_submission_services import accept_session_submission
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        document = {'submission_token': str(uuid.uuid4()), 'client_clicked_at': timezone.now().isoformat(),
            'expected_versions': [{'attempt_id': row.pk, 'response_version': row.response_version,
                                   'target_event_id': None} for row in session.attempts.order_by('pk')]}
        job = accept_session_submission(student=self.student, session_id=session.pk, document=document)
        schedule = session.schedule_revision
        ScheduleRevision.objects.create(class_task=self.task, revision_no=schedule.revision_no + 1,
            opens_at=schedule.opens_at, closes_at=schedule.closes_at,
            total_time_limit_seconds=schedule.total_time_limit_seconds,
            per_question_time_limit_seconds=schedule.per_question_time_limit_seconds, created_by=self.teacher)
        self.client.force_login(self.student)
        response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/resume/',
            data={}, content_type='application/json')
        self.assertEqual(response.status_code, 409, response.content)
        session.refresh_from_db()
        self.assertEqual(session.status, 'submitting')
        self.assertEqual(session.terminal_at, job.accepted_at)

    def test_expired_item_can_be_reviewed_without_granting_answer_writes(self):
        from .prepared_services import activate_prepared_session, prepared_runtime
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        attempt = session.attempts.select_related('current_response_snapshot').get()
        attempt.session = session
        attempt.current_effective_deadline_at = timezone.now() - timedelta(seconds=1)
        attempt.status = 'timed_out'
        payload = prepared_runtime(attempt)
        self.assertTrue(payload['entry_allowed'])
        self.assertFalse(payload['response_write_allowed'])
        self.assertNotEqual(payload['remaining_seconds'], 0)

    def test_preparation_repair_is_coalesced_and_background_only(self):
        from .prepared_models import PreparedSessionRepair
        from .prepared_services import enqueue_preparation_repair, run_preparation_repair
        participant = self.task.participant_snapshots.get(student=self.student)
        artifact = ExperimentSession.objects.get(class_task=self.task, student=self.student).prepared_artifact
        artifact.ready = False
        artifact.save(update_fields=['ready'])
        first = enqueue_preparation_repair(participant)
        again = enqueue_preparation_repair(participant)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(PreparedSessionRepair.objects.count(), 1)
        artifact.refresh_from_db()
        self.assertFalse(artifact.ready)
        self.assertTrue(run_preparation_repair())
        artifact.refresh_from_db()
        self.assertTrue(artifact.ready)
        first.refresh_from_db()
        self.assertEqual(first.status, 'completed')
        self.assertFalse(run_preparation_repair())

    def test_background_repair_restores_missing_ciphertext_with_the_same_frozen_descriptor(self):
        from .prepared_services import enqueue_preparation_repair, run_preparation_repair
        participant = self.task.participant_snapshots.get(student=self.student)
        artifact = ExperimentSession.objects.get(class_task=self.task, student=self.student).prepared_artifact
        descriptor = artifact.first_runtime['prepared_paper']['question']
        path = Path(self.paper_directory.name) / f"{descriptor['sha256']}.json"
        before = path.read_bytes()
        path.unlink()
        artifact.ready = False
        artifact.save(update_fields=['ready'])
        enqueue_preparation_repair(participant)
        run_preparation_repair()
        artifact.refresh_from_db()
        self.assertTrue(artifact.ready)
        self.assertEqual(artifact.first_runtime['prepared_paper']['question'], descriptor)
        self.assertEqual(path.read_bytes(), before)

    def test_completed_repair_requeues_once_when_preparation_loses_readiness(self):
        from .prepared_services import enqueue_preparation_repair, run_preparation_repair
        participant = self.task.participant_snapshots.get(student=self.student)
        artifact = ExperimentSession.objects.get(class_task=self.task, student=self.student).prepared_artifact
        artifact.ready = False
        artifact.save(update_fields=['ready'])
        job = enqueue_preparation_repair(participant)
        self.assertTrue(run_preparation_repair())
        self.assertEqual(enqueue_preparation_repair(participant).status, 'completed')
        artifact.ready = False
        artifact.save(update_fields=['ready'])
        self.assertEqual(enqueue_preparation_repair(participant).pk, job.pk)
        self.assertEqual(enqueue_preparation_repair(participant).status, 'queued')
        self.assertTrue(run_preparation_repair())
        artifact.refresh_from_db()
        self.assertTrue(artifact.ready)

    def test_backfill_command_enqueues_unstarted_participants_without_mutating_started_sessions(self):
        from django.core.management import call_command
        from .prepared_models import PreparedSessionRepair
        from .prepared_services import activate_prepared_session
        started, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        unstarted = ExperimentSession.objects.filter(class_task=self.task, started_at=None).get()
        # Simulate a task published before prepared resources existed.
        participant_id = unstarted.participant_snapshot_id
        unstarted.prepared_artifact.delete()
        call_command('run_preparation_repairs', enqueue_task=self.task.pk, enqueue_only=True)
        self.assertEqual(list(PreparedSessionRepair.objects.values_list('participant_id', flat=True)), [participant_id])
        self.assertFalse(hasattr(ExperimentSession.objects.get(pk=unstarted.pk), 'prepared_artifact'))
        with patch('apps.assessment.management.commands.run_preparation_repairs.close_old_connections'):
            call_command('run_preparation_repairs', once=True)
        unstarted.refresh_from_db()
        self.assertTrue(unstarted.prepared_artifact.ready)
        self.assertIsNone(unstarted.started_at)
        started.refresh_from_db()
        self.assertEqual(started.status, 'in_progress')
        self.assertEqual(started.visit_event_count, 1)

    def test_readonly_return_records_real_view_after_item_grace_without_allowing_response(self):
        from .prepared_services import activate_prepared_session
        from .recording_capture_services import validate_close_recording_time
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        attempt = session.attempts.get()
        attempt.session = session
        attempt.current_effective_deadline_at = timezone.now() - timedelta(hours=1)
        attempt.status = 'timed_out'
        self.assertIsNone(validate_close_recording_time(session, attempt,
            request_received_at=timezone.now(), event_type='item_left'))
        self.assertIsNotNone(validate_close_recording_time(session, attempt,
            request_received_at=timezone.now(), event_type='response_changed'))

    def test_expired_open_item_entry_closes_answer_and_keeps_navigation_open(self):
        from .prepared_services import activate_prepared_session
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        attempt = session.attempts.get()
        attempt.current_effective_deadline_at = timezone.now() - timedelta(seconds=1)
        attempt.save(update_fields=['current_effective_deadline_at'])
        self.client.force_login(self.student)
        now = timezone.now().isoformat()
        response = self.client.post(f'/api/v1/student/sessions/{session.pk}/questions/{attempt.assignment_id}/',
            data={'navigation_id': str(uuid.uuid4()), 'reason': 'resume', 'client_occurred_at': now,
                  'client_time_origin': now, 'client_monotonic_ms': 0}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json().get('data', response.json())
        self.assertTrue(payload['entry_allowed'])
        self.assertFalse(payload['response_write_allowed'])
        attempt.refresh_from_db()
        self.assertIn(attempt.status, ('timed_out', 'unanswered', 'submitted'))
        session.refresh_from_db()
        self.assertEqual(session.status, 'in_progress')

    def test_batch_readonly_render_does_not_authorize_following_response_event(self):
        from .prepared_services import activate_prepared_session
        from .event_batch_services import append_event_batch, ProcessConflictError
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        attempt = session.attempts.get()
        attempt.current_effective_deadline_at = timezone.now() - timedelta(seconds=1)
        attempt.save(update_fields=['current_effective_deadline_at'])
        now = timezone.now().isoformat()
        base = {'session_id': session.pk, 'attempt_id': attempt.pk,
                'client_wall_time_utc': now, 'client_time_origin': now,
                'quality_flags': [], 'app_version': 'prepared-test'}
        document = {'client_instance_id': str(uuid.uuid4()), 'events': [
            {**base, 'event_id': str(uuid.uuid4()), 'event_type': 'item_rendered', 'event_version': 1,
             'client_sequence_no': 1, 'client_monotonic_ms': 1, 'payload': {}},
            {**base, 'event_id': str(uuid.uuid4()), 'event_type': 'response_input_started', 'event_version': 1,
             'client_sequence_no': 2, 'client_monotonic_ms': 2, 'payload': {'question_type': 'single_choice'}},
        ]}
        with self.assertRaises(ProcessConflictError):
            append_event_batch(student=self.student, document=document)

    def test_prepared_resources_do_not_enter_observed_research_or_teaching_cohorts(self):
        from apps.analytics.data_scope import DataScope
        from apps.analytics.teaching_scope import TeachingScope
        self.assertEqual(DataScope(course=self.course).sessions().count(), 0)
        self.assertEqual(TeachingScope(course=self.course).sessions().count(), 0)
        self.assertEqual(DataScope(course=self.course).session_references(), {})

    def test_activation_query_write_and_lock_counts_are_independent_of_twenty_or_two_hundred_items(self):
        from apps.question_bank.models import Question, QuestionConcept
        from .prepared_services import activate_prepared_session
        concept = self.question.primary_concept
        counts = []
        for count in (20, 200):
            for sequence in range(self.bank.questions.count() + 1, count + 1):
                question = Question.objects.create(bank=self.bank, sequence=sequence, code=f'BOUND-{sequence}',
                    question_type=Question.Type.SHORT, text=f'仅发布时处理的正文 {sequence}',
                    correct_answer='说明')
                QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
            _, task = self.publish_task(name=f'激活固定成本{count}', feedback_mode='none')
            with CaptureQueriesContext(connection) as queries:
                _, receipt = activate_prepared_session(student=self.student, task_id=task.pk,
                    client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
            sql = [row['sql'] for row in queries]
            counts.append((len(sql), sum(query.startswith(('INSERT', 'UPDATE', 'DELETE')) for query in sql),
                           sum('FOR ' in query and 'UPDATE' in query for query in sql)))
            self.assertEqual(receipt['runtime']['progress']['total'], count)
            self.assertFalse(any(query.startswith('SELECT') and '"text"' in query for query in sql))
            self.assertFalse(any('FROM "experiments_classexperimenttask"' in query and 'FOR UPDATE' in query for query in sql))
        self.assertLessEqual(abs(counts[0][0] - counts[1][0]), 6, counts)
        self.assertLessEqual(abs(counts[0][1] - counts[1][1]), 2, counts)
        self.assertEqual(counts[0][2], counts[1][2], counts)

    def test_repeated_activation_preserves_time_and_single_first_visit(self):
        self.assertTrue(ExperimentSession.objects.filter(class_task=self.task).exists())
        from .prepared_services import activate_prepared_session
        first, receipt = activate_prepared_session(
            student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4(),
        )
        again, replay = activate_prepared_session(
            student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4(),
        )
        self.assertEqual(first.started_at, again.started_at)
        self.assertEqual(receipt['runtime']['entry_receipt'], replay['runtime']['entry_receipt'])
        self.assertEqual(AssessmentEvent.objects.filter(session=first, event_type='session_started').count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(session=first, event_type='item_entered').count(), 1)

    def test_activation_replay_projects_current_draft_and_item_write_permission(self):
        from .prepared_services import activate_prepared_session
        client_id = uuid.uuid4()
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=client_id, navigation_id=uuid.uuid4())
        attempt = session.attempts.get()
        attempt.current_effective_deadline_at = timezone.now() - timedelta(seconds=1)
        attempt.status = 'timed_out'
        attempt.draft_answer = 'B'
        attempt.save(update_fields=['current_effective_deadline_at', 'status', 'draft_answer'])
        _, replay = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=client_id, navigation_id=uuid.uuid4())
        self.assertTrue(replay['runtime']['entry_allowed'])
        self.assertFalse(replay['runtime']['response_write_allowed'])
        self.assertEqual(replay['runtime']['draft_answer'], 'B')
        self.assertNotIn('initial_writer_lease', replay['runtime'])

    def test_ordinary_prepared_entry_returns_current_lease_and_readonly_replay_omits_it(self):
        from .prepared_services import activate_prepared_session
        client_id = uuid.uuid4()
        session, activation = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=client_id, navigation_id=uuid.uuid4())
        attempt = session.attempts.get()
        self.client.force_login(self.student)
        document = {'reason': 'resume', 'navigation_id': str(uuid.uuid4()),
            'client_instance_id': str(client_id), 'client_occurred_at': timezone.now().isoformat(),
            'client_monotonic_ms': '1.000', 'client_time_origin': '2026-10-16T00:00:00Z'}
        url = f'/api/v1/student/sessions/{session.pk}/questions/{attempt.assignment_id}/'
        first = self.client.post(url, document, content_type='application/json')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['data']['initial_writer_lease'], activation['runtime']['initial_writer_lease'])
        attempt.status = 'submitted'
        attempt.save(update_fields=['status'])
        replay = self.client.post(url, document, content_type='application/json')
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertFalse(replay.json()['data']['response_write_allowed'])
        self.assertNotIn('initial_writer_lease', replay.json()['data'])

    def test_prepared_projection_preserves_initial_and_current_research_tool_rules(self):
        from .prepared_services import activate_prepared_session, prepared_runtime
        from .research_tool_services import record_tool_action
        config = {'check_enabled': True, 'doubt_enabled': True, 'rules': [{
            'id': 'check1', 'tool': 'check', 'trigger': 'item_enter', 'question_code': '',
            'after_seconds': 0, 'max_exposures': 1, 'required': True, 'hint_id': ''}]}
        _, task = self.publish_task(name='冻结研究工具', feedback_mode='none', research_tools=config)
        session, receipt = activate_prepared_session(student=self.student, task_id=task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        self.assertEqual([rule['id'] for rule in receipt['runtime']['research_tools']['rules']], ['check1'])
        attempt = session.attempts.select_related('assignment__snapshot', 'current_response_snapshot').get()
        attempt.session = session
        record_tool_action(student=self.student, attempt_id=attempt.pk, tool='doubt', stage='mark_added',
            operation_id=str(uuid.uuid4()))
        self.assertTrue(prepared_runtime(attempt)['research_tools']['state']['doubt'])

    def test_legacy_skip_endpoint_cannot_finish_a_prepared_paper_synchronously(self):
        from .prepared_services import activate_prepared_session
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        attempt = session.attempts.get()
        self.client.force_login(self.student)
        response = self.client.post(f'/api/v1/student/attempts/{attempt.pk}/skip/',
            data={}, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        session.refresh_from_db()
        self.assertEqual(session.status, 'in_progress')
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'visited')

    def test_inline_zero_is_saved_as_draft_without_fabricating_prompt_or_submission(self):
        from .prepared_services import activate_prepared_session, save_inline_confidence
        _, task = self.publish_task(name='可选信心', feedback_mode='none', confidence_mode='all')
        session, _ = activate_prepared_session(student=self.student, task_id=task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        attempt = session.attempts.select_related('assignment__snapshot').get()
        attempt.session = session
        save_inline_confidence(attempt, {'probability': 0, 'answer_event_id': None})
        value = ConfidenceAssignment.objects.get(attempt=attempt)
        self.assertEqual(value.draft_probability, 0)
        self.assertIsNone(value.probability)
        self.assertIsNone(value.committed_at)
        self.assertIsNone(value.prompt_shown_at)
        self.assertFalse(AssessmentEvent.objects.filter(attempt=attempt,
            event_type__in=('confidence_prompt_shown', 'confidence_committed', 'response_committed')).exists())
        save_inline_confidence(attempt, {'probability': None, 'answer_event_id': None})
        value.refresh_from_db()
        self.assertIsNone(value.draft_probability)

    def test_inline_confidence_rejects_reference_to_another_answer(self):
        from .prepared_services import activate_prepared_session, save_inline_confidence
        _, task = self.publish_task(name='信心版本绑定', feedback_mode='none', confidence_mode='all')
        session, _ = activate_prepared_session(student=self.student, task_id=task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        attempt = session.attempts.select_related('assignment__snapshot').get()
        attempt.session = session
        with self.assertRaises(ValidationError):
            save_inline_confidence(attempt, {'probability': 50, 'answer_event_id': str(uuid.uuid4())})
        self.assertIsNone(ConfidenceAssignment.objects.get(attempt=attempt).draft_probability)
