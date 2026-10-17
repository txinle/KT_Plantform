"""Prepared startup admission, optional tools and bounded task-list reads."""
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.db import connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .models import AssessmentEvent, Attempt, ExperimentSession
from .test_student_api import StudentApiFixtureMixin


class PreparedFixtureMixin(StudentApiFixtureMixin):
    prepared_paper_fixture = True
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = override_settings(ASSESSMENT_PAPER_ROOT=Path(directory.name))
        config.enable()
        self.addCleanup(config.disable)
        super().setUp()


@override_settings(ASSESSMENT_PREPARED_ENABLED=True)
class PreparedProjectionRemediationTests(PreparedFixtureMixin, TestCase):
    def test_empty_tools_omit_initial_and_recovered_entry_even_for_old_projection(self):
        from .prepared_services import activate_prepared_session
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        artifact = session.prepared_artifact
        self.assertNotIn('research_tools_url', artifact.first_runtime['actions'])
        session, initial = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        self.assertNotIn('research_tools_url', initial['runtime']['actions'])
        projection = session.attempts.get().prepared_projection
        projection.runtime['actions']['research_tools_url'] = '/old/projection/tools/'
        projection.save(update_fields=['runtime'])
        _, replay = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        self.assertNotIn('research_tools_url', replay['runtime']['actions'])

    def test_future_tool_rule_keeps_initial_and_recovered_entry(self):
        from .prepared_services import activate_prepared_session
        config = {'rules': [{'id': 'later', 'tool': 'confidence', 'trigger': 'elapsed_seconds',
            'question_code': '', 'after_seconds': 600, 'max_exposures': 1,
            'required': False, 'hint_id': ''}]}
        _, task = self.publish_task(name='未来工具规则', feedback_mode='none', research_tools=config)
        for _ in range(2):
            _, receipt = activate_prepared_session(student=self.student, task_id=task.pk,
                client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
            self.assertIn('research_tools_url', receipt['runtime']['actions'])
            self.assertEqual(receipt['runtime']['research_tools']['rules'], [])

    def test_task_list_counts_statuses_without_loading_snapshot_or_attempt_rows(self):
        from .api.views import _student_task_queryset, _task_projection
        from .prepared_services import activate_prepared_session
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        for status in Attempt.Status.values:
            session.attempts.update(status=status)
            with CaptureQueriesContext(connection) as queries:
                task = _student_task_queryset(self.student).get(pk=self.task.pk)
                result = _task_projection(task, course=self.course)
            self.assertEqual(result['progress'], {'total': 1,
                'completed': int(status in ('submitted', 'timed_out', 'unanswered'))})
            selects = [row['sql'] for row in queries if row['sql'].startswith('SELECT')]
            self.assertFalse(any('"draft_answer"' in sql or '"correct_answer"' in sql
                                 or '"assignment_snapshot"' in sql for sql in selects), selects)
            self.assertFalse(any(sql.startswith('SELECT "assessment_attempt".') for sql in selects), selects)
            self.assertFalse(any('"experiments_questionsnapshot"."text"' in sql for sql in selects), selects)

    def test_prepared_task_uses_preview_count_then_version_count_and_start_action(self):
        from .api.views import _student_task_queryset, _task_projection
        from apps.experiments.models import PublishPreviewParticipant
        for preview_count in (7, None):
            if preview_count is None:
                PublishPreviewParticipant.objects.filter(class_result=self.task.preview_class_result,
                    student=self.student).delete()
            else:
                PublishPreviewParticipant.objects.filter(class_result=self.task.preview_class_result,
                    student=self.student).update(item_count=preview_count)
            task = _student_task_queryset(self.student).get(pk=self.task.pk)
            result = _task_projection(task, course=self.course)
            self.assertEqual(result['progress'], {'total': preview_count or 1, 'completed': 0})
            self.assertEqual(result['primary_action'], 'start')

    def test_reopened_expired_task_preserves_recovery_predicates_without_loading_answers(self):
        from .api.views import _student_task_queryset, _task_projection
        from .prepared_services import activate_prepared_session
        from apps.experiments.models import Experiment, ScheduleRevision
        create_experiment = Experiment.objects.create
        with patch.object(Experiment.objects, 'create', side_effect=lambda **values:
                create_experiment(**values, time_mode='per_question', per_question_time_limit_seconds=30)):
            _, recovery_task = self.publish_task(name='recovery reads', feedback_mode='none')
        session, _ = activate_prepared_session(student=self.student, task_id=recovery_task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        session.status, session.terminal_at = 'expired', timezone.now()
        session.save(update_fields=['status', 'terminal_at'])
        original = session.schedule_revision
        ScheduleRevision.objects.create(class_task=recovery_task, revision_no=original.revision_no + 1,
            opens_at=original.opens_at, closes_at=original.closes_at,
            per_question_time_limit_seconds=60, created_by=self.teacher)
        for old_limit, opened, deadline_after_terminal, allowed in (
                (None, True, False, True), (30, True, False, True), (60, True, False, False),
                (90, False, False, True), (90, True, True, True)):
            session.attempts.update(status='timed_out',
                first_opened_at=session.started_at if opened else None,
                per_item_time_limit_seconds=old_limit,
                item_deadline_at=session.terminal_at + timedelta(seconds=1) if deadline_after_terminal else None)
            with CaptureQueriesContext(connection) as queries:
                task = _student_task_queryset(self.student).get(pk=recovery_task.pk)
                result = _task_projection(task, course=self.course)
            self.assertEqual(result['primary_action'], 'resume' if allowed else 'view_result')
            self.assertFalse(any('"draft_answer"' in row['sql'] or '"final_answer"' in row['sql']
                                 for row in queries), [row['sql'] for row in queries])

    def test_twenty_and_two_hundred_item_lists_use_fixed_reads_without_paper_content(self):
        from apps.question_bank.models import Question, QuestionConcept
        from .api.views import _student_task_queryset, _task_projection
        from .prepared_services import activate_prepared_session
        concept = self.question.primary_concept
        counts = []
        for count in (20, 200):
            for sequence in range(self.bank.questions.count() + 1, count + 1):
                question = Question.objects.create(bank=self.bank, sequence=sequence, code=f'LIST-{sequence}',
                    question_type=Question.Type.SHORT, text=f'paper body {sequence}', correct_answer='answer')
                QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
            _, published = self.publish_task(name=f'list count {count}', feedback_mode='none')
            activate_prepared_session(student=self.student, task_id=published.pk,
                client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
            with CaptureQueriesContext(connection) as queries:
                task = _student_task_queryset(self.student).get(pk=published.pk)
                result = _task_projection(task, course=self.course)
            self.assertEqual(result['progress'], {'total': count, 'completed': 0})
            self.assertFalse(any('"text"' in row['sql'] or '"draft_answer"' in row['sql']
                                 or '"assignment_snapshot"' in row['sql'] for row in queries))
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])


@override_settings(ASSESSMENT_PREPARED_ENABLED=True, ASSESSMENT_PREPARED_ADMISSION_MODE='postgres_transaction',
    ASSESSMENT_ADMISSION_ENABLED=True, ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True,
    ASSESSMENT_ADMISSION_CAPACITY=1)
class PreparedTransactionAdmissionTests(PreparedFixtureMixin, TransactionTestCase):
    def start(self, student=None):
        from django.test import Client
        client = Client()
        client.force_login(student or self.student)
        return client.post(f'/api/v1/student/tasks/{self.task.pk}/start/',
            {'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4())},
            content_type='application/json')

    def test_postgres_activation_and_replay_use_no_redis_or_cancellation_fence(self):
        with patch('apps.assessment.api.prepared_start.AdmissionCoordinator',
                   side_effect=AssertionError('Redis coordinator must not run')), \
             patch('apps.assessment.prepared_services.PreparedActivationCancellation.objects.filter',
                   side_effect=AssertionError('Redis cancellation must not run')):
            first, replay = self.start(), self.start()
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(replay.status_code, 200, replay.content)
        receipt = first.json().get('data', first.json())
        artifact = ExperimentSession.objects.get(class_task=self.task, student=self.student).prepared_artifact
        self.assertEqual(str(artifact.activation_execution_id), receipt['admission_execution_id'])
        self.assertIsNotNone(artifact.activation_execution_id)
        self.assertEqual(AssessmentEvent.objects.filter(session=artifact.session, event_type='session_started').count(), 1)

    def test_live_activation_holds_slot_and_same_student_returns_executing(self):
        self.assertEqual(connection.vendor, 'postgresql')
        from . import prepared_services
        entered, release = threading.Event(), threading.Event()
        append = prepared_services.append_assessment_event
        def hold_activation(**kwargs):
            if kwargs['event_type'] == 'session_started':
                entered.set()
                if not release.wait(15):
                    raise RuntimeError('test activation did not release')
            return append(**kwargs)
        def activate():
            try:
                return self.start()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool, \
             patch.object(prepared_services, 'append_assessment_event', side_effect=hold_activation):
            future = pool.submit(activate)
            try:
                self.assertTrue(entered.wait(10))
                duplicate = self.start()
                busy = self.start(self.peer)
                self.assertEqual(duplicate.status_code, 202, duplicate.content)
                self.assertEqual(busy.status_code, 429, busy.content)
                self.assertEqual(busy.json()['error']['code'], 'START_BUSY')
                peer_session = ExperimentSession.objects.get(class_task=self.task, student=self.peer)
                self.assertIsNone(peer_session.started_at)
                self.assertEqual(peer_session.status, 'prepared')
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10).status_code, 201)
        self.assertEqual(self.start(self.peer).status_code, 201)

    def test_recovery_ignores_slots_occupied_by_other_activation(self):
        self.assertEqual(self.start().status_code, 201)
        from .prepared_admission import acquire_activation_slot
        held, release = threading.Event(), threading.Event()
        def holder():
            try:
                with transaction.atomic():
                    acquire_activation_slot(student_id=self.peer.pk)
                    held.set()
                    release.wait(15)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(holder)
            try:
                self.assertTrue(held.wait(10))
                self.assertEqual(self.start().status_code, 200)
            finally:
                release.set()
            future.result(timeout=10)

    def test_rollback_releases_slot_and_removes_all_activation_facts(self):
        from .prepared_services import activate_prepared_session
        with patch('apps.assessment.prepared_services.append_assessment_event', side_effect=RuntimeError('rollback')):
            with self.assertRaisesMessage(RuntimeError, 'rollback'):
                activate_prepared_session(student=self.student, task_id=self.task.pk,
                    client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.assertIsNone(session.started_at)
        self.assertFalse(session.prepared_artifact.activation_receipt)
        self.assertEqual(self.start(self.peer).status_code, 201)

    def test_database_failure_returns_explicit_unavailable_without_redis_fallback(self):
        from django.db import OperationalError
        with patch('apps.assessment.api.prepared_start.activate_prepared_session', side_effect=OperationalError('DB down')), \
             patch('apps.assessment.api.prepared_start.AdmissionCoordinator', side_effect=AssertionError('Redis fallback')):
            response = self.start()
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()['error']['code'], 'ADMISSION_UNAVAILABLE')

    def test_backend_loss_before_commit_rolls_back_started_timer_visit_and_receipt(self):
        from . import prepared_services
        entered, finish = threading.Event(), threading.Event()
        backend = []
        append = prepared_services.append_assessment_event
        def hold_after_writes(**kwargs):
            if kwargs['event_type'] == 'session_started':
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    backend.append(cursor.fetchone()[0])
                entered.set()
                finish.wait(10)
            return append(**kwargs)
        def activate():
            try:
                return self.start()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool, \
             patch.object(prepared_services, 'append_assessment_event', side_effect=hold_after_writes):
            future = pool.submit(activate)
            try:
                self.assertTrue(entered.wait(5))
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_terminate_backend(%s)', [backend[0]])
                    self.assertTrue(cursor.fetchone()[0])
            finally:
                finish.set()
            self.assertEqual(future.result(timeout=5).status_code, 503)
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.assertIsNone(session.started_at)
        self.assertIsNone(session.personal_deadline_at)
        self.assertEqual(session.visit_event_count, 0)
        self.assertFalse(session.visit_events.exists())
        self.assertFalse(session.prepared_artifact.activation_receipt)
        self.assertIsNone(session.prepared_artifact.activation_execution_id)
        self.assertEqual(self.start(self.peer).status_code, 201)
        self.assertEqual(self.start().status_code, 201)
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='session_started').count(), 1)

    def test_authoritative_course_and_task_rejections_leave_no_start_facts(self):
        self.course.status = 'archived'
        self.course.save(update_fields=['status'])
        response = self.start()
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['error']['code'], 'COURSE_ACCESS_DENIED')
        self.course.status = 'active'
        self.course.save(update_fields=['status'])
        from apps.experiments.publication_services import manual_close_task
        manual_close_task(self.task, self.teacher, reason='deadline authority test')
        self.assertEqual(self.start().status_code, 409)
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.assertIsNone(session.started_at)
        self.assertFalse(AssessmentEvent.objects.filter(session=session).exists())


@override_settings(ASSESSMENT_ADMISSION_CAPACITY=2)
class PreparedSlotConcurrencyTests(TransactionTestCase):
    def test_two_slots_are_bounded_rotated_and_released_on_commit_and_rollback(self):
        from .prepared_admission import ActivationBusy, acquire_activation_slot
        releases = [threading.Event(), threading.Event()]
        entered = [threading.Event(), threading.Event()]
        def holder(index):
            try:
                try:
                    with transaction.atomic():
                        slot = acquire_activation_slot(student_id=0)
                        entered[index].set()
                        releases[index].wait(10)
                        if index == 1:
                            raise ValueError('rollback')
                        return slot
                except ValueError:
                    return slot
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(holder, 0)
            self.assertTrue(entered[0].wait(5))
            second = pool.submit(holder, 1)
            try:
                self.assertTrue(entered[1].wait(5))
                with self.assertRaises(ActivationBusy), transaction.atomic(), CaptureQueriesContext(connection) as queries:
                    acquire_activation_slot(student_id=0)
                self.assertEqual(sum('pg_try_advisory_xact_lock' in row['sql'] for row in queries), 2)
                releases[0].set()
                self.assertEqual(first.result(timeout=5), 0)
                with transaction.atomic():
                    self.assertEqual(acquire_activation_slot(student_id=0), 0)
            finally:
                for release in releases:
                    release.set()
            self.assertEqual(second.result(timeout=5), 1)
        with transaction.atomic():
            self.assertEqual(acquire_activation_slot(student_id=1), 1)

    @override_settings(ASSESSMENT_ADMISSION_CAPACITY=1)
    def test_database_connection_end_releases_transaction_slot(self):
        from django.db import DatabaseError
        from .prepared_admission import acquire_activation_slot
        entered, finish = threading.Event(), threading.Event()
        backend = []
        def holder():
            try:
                try:
                    with transaction.atomic():
                        acquire_activation_slot(student_id=0)
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()')
                            backend.append(cursor.fetchone()[0])
                        entered.set()
                        finish.wait(10)
                except DatabaseError:
                    pass
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(holder)
            try:
                self.assertTrue(entered.wait(5))
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_terminate_backend(%s)', [backend[0]])
                    self.assertTrue(cursor.fetchone()[0])
                with transaction.atomic():
                    self.assertEqual(acquire_activation_slot(student_id=0), 0)
            finally:
                finish.set()
            future.result(timeout=5)

    def test_non_postgres_backend_fails_closed(self):
        from .admission import AdmissionUnavailable
        from .prepared_admission import acquire_activation_slot
        with patch.object(connection, 'vendor', 'sqlite'), transaction.atomic():
            with self.assertRaises(AdmissionUnavailable):
                acquire_activation_slot(student_id=0)
