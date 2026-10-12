"""PostgreSQL proof that personal result commits have no cohort write lock."""
from apps.api.test_support import domain_payload
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import uuid
from unittest.mock import patch

from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings

from apps.assessment.models import ExperimentSession, SubmissionEvent
from apps.assessment.services import get_or_create_session, record_visit, save_draft, submit_attempt
from apps.assessment.test_student_api import StudentApiFixtureMixin


@override_settings(STUDENT_SHARED_RESULTS_ENABLED=True,
                   STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({'*'}),
                   CLASS_CENTER_ENABLED=True, STUDENT_LEARNING_ANALYTICS_ENABLED=True)
class PersonalResultWriteTests(StudentApiFixtureMixin, TransactionTestCase):
    def _attempts(self):
        from apps.assessment.event_services import _register_schema_artifact
        for name in ('response_committed', 'attempt_closed'):
            _register_schema_artifact(name, 1)
        sessions = [get_or_create_session(self.experiment, student, class_task=self.task)
                    for student in (self.student, self.peer)]
        attempts = [record_visit(session.pk, session.assignments.get().pk) for session in sessions]
        for attempt in attempts:
            save_draft(attempt.pk, 'B')
        return sessions, attempts

    def test_peer_can_commit_while_first_students_completed_submission_is_uncommitted(self):
        sessions, attempts = self._attempts()
        self.task.refresh_from_db()
        before = self.task.result_revision

        def submit_peer():
            connections.close_all()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '750ms'")
                return submit_attempt(attempts[1].pk, uuid.uuid4()).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                submit_attempt(attempts[0].pk, uuid.uuid4())
                self.assertEqual(pool.submit(submit_peer).result(timeout=5), attempts[1].pk)
        self.task.refresh_from_db()
        self.assertEqual(self.task.result_revision, before)
        self.assertEqual(SubmissionEvent.objects.filter(attempt__in=attempts).count(), 2)
        for session in sessions:
            session.refresh_from_db()
            self.assertEqual(session.result_revision, 1)

    def test_same_students_second_submit_waits_then_reuses_committed_fact(self):
        _, attempts = self._attempts()
        entered = Event()
        key = uuid.uuid4()

        def resubmit():
            connections.close_all()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET statement_timeout = '5s'")
                entered.set()
                return submit_attempt(attempts[0].pk, key).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                submit_attempt(attempts[0].pk, key)
                future = pool.submit(resubmit)
                self.assertTrue(entered.wait(timeout=2))
                self.assertFalse(future.done())
            self.assertEqual(future.result(timeout=5), attempts[0].pk)
        self.assertEqual(SubmissionEvent.objects.filter(attempt=attempts[0]).count(), 1)

    def test_rollback_after_result_revision_restores_fact_and_personal_revision(self):
        sessions, attempts = self._attempts()
        before = sessions[0].result_revision
        with self.assertRaisesRegex(RuntimeError, 'rollback after all writes'):
            with transaction.atomic():
                submit_attempt(attempts[0].pk, uuid.uuid4())
                raise RuntimeError('rollback after all writes')
        sessions[0].refresh_from_db()
        self.assertEqual(sessions[0].result_revision, before)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempts[0]).exists())


@override_settings(STUDENT_SHARED_RESULTS_ENABLED=True,
                   STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({'*'}),
                   CLASS_CENTER_ENABLED=True, STUDENT_LEARNING_ANALYTICS_ENABLED=True)
class ComparisonSourceSignatureTests(StudentApiFixtureMixin, TransactionTestCase):
    def _completed(self, student=None):
        session = get_or_create_session(self.experiment, student or self.student, class_task=self.task)
        self.complete_session(session)
        return session

    def _detail(self, *, status=False):
        self.client.force_login(self.student)
        return domain_payload(self.client.get(
            f'/api/v1/student/courses/{self.course.pk}/learning/experiments/{self.experiment.pk}/',
            {'status_only': '1'} if status else {}))

    def test_peer_grade_invalidates_summary_and_status_without_touching_class_revision(self):
        from apps.analytics.student_comparisons import run_comparison_jobs
        from apps.assessment.services import grade_submission
        self._completed()
        peer = self._completed(self.peer)
        self.task.refresh_from_db()
        revision = self.task.result_revision
        run_comparison_jobs(limit=1)
        ready = self._detail(status=True)
        self.assertEqual(ready['comparison_status'], 'ready')
        grade_submission(SubmissionEvent.objects.get(attempt__session=peer).pk,
                         score=0, max_score=1, is_correct=False)
        self.task.refresh_from_db()
        self.assertEqual(self.task.result_revision, revision)
        changed = self._detail(status=True)
        self.assertNotEqual(ready['version'], changed['version'])
        self.assertEqual(changed['comparison_status'], 'updating')
        self.assertEqual(self._detail()['comparison_status'], 'updating')
        run_comparison_jobs(limit=1)
        self.assertEqual(self._detail()['comparison_status'], 'ready')

    def test_source_signature_tracks_insert_status_revision_and_delete(self):
        from apps.analytics.student_comparisons import comparison_source_signatures
        from apps.analytics.teaching.scope import analytics_read_transaction
        def signature():
            with analytics_read_transaction():
                return comparison_source_signatures((self.task,))[self.task.pk]
        seen = [signature()]
        session = ExperimentSession.objects.create(version_id=self.task.release_snapshot_id,
            class_snapshot_id=self.task.class_snapshot_id, class_task=self.task, student=self.student)
        seen.append(signature())
        ExperimentSession.objects.filter(pk=session.pk).update(result_revision=1)
        seen.append(signature())
        from django.utils import timezone
        ExperimentSession.objects.filter(pk=session.pk).update(
            status=ExperimentSession.Status.INVALIDATED, terminal_at=timezone.now())
        seen.append(signature())
        self.assertEqual(len(set(seen)), 4)
        # Delete a session with no appended events, preserving protected fact rules.
        session.delete()
        self.assertEqual(signature(), seen[0])

    def test_source_signature_uses_batched_scalar_queries_and_stable_task_order(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from apps.analytics.student_comparisons import comparison_source_signatures
        from apps.analytics.teaching.scope import analytics_read_transaction
        other_experiment, other_task = self.publish_task(name='second', feedback_mode=self.feedback_mode)
        get_or_create_session(self.experiment, self.student, class_task=self.task)
        get_or_create_session(other_experiment, self.student, class_task=other_task)
        with analytics_read_transaction():
            with CaptureQueriesContext(connection) as queries:
                first = comparison_source_signatures((self.task, other_task))
            self.assertEqual(first, comparison_source_signatures((other_task, self.task)))
        self.assertEqual(len(queries), 2)
        sql = '\n'.join(q['sql'].lower() for q in queries)
        for forbidden in ('assessment_graderecord', 'assessment_assessmentevent', 'assessment_attempt', 'final_answer'):
            self.assertNotIn(forbidden, sql)

    def test_source_change_during_task_build_cannot_publish_ready_old_content(self):
        from apps.analytics import student_comparisons as comparisons
        from apps.analytics.models import StudentClassTaskComparison
        from apps.assessment.services import grade_submission
        self._completed()
        peer = self._completed(self.peer)
        submission_id = SubmissionEvent.objects.get(attempt__session=peer).pk
        build = comparisons._build_summary

        def grade_peer():
            connections.close_all()
            try:
                grade_submission(submission_id, score=0, max_score=1, is_correct=False)
            finally:
                connections.close_all()

        def build_with_concurrent_grade(task):
            payload = build(task)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(grade_peer).result(timeout=5)
            return payload

        with patch.object(comparisons, '_build_summary', side_effect=build_with_concurrent_grade):
            comparisons.run_comparison_jobs(limit=1)
        row = StudentClassTaskComparison.objects.get(class_task=self.task)
        self.assertEqual(row.status, 'queued')
        self.assertIsNone(row.generated_at)
        self.assertEqual(comparisons.run_comparison_jobs(limit=1)['completed'], 1)
        self.assertEqual(self._detail()['comparison_status'], 'ready')

    def test_task_facts_and_signature_share_one_repeatable_read_snapshot(self):
        from apps.analytics import student_comparisons as comparisons
        from apps.analytics.teaching.scope import analytics_read_transaction
        from apps.assessment.services import grade_submission
        self._completed()
        peer = self._completed(self.peer)
        submission_id = SubmissionEvent.objects.get(attempt__session=peer).pk
        with analytics_read_transaction():
            before = comparisons.comparison_source_signatures((self.task,))[self.task.pk]
        build = comparisons._build_summary

        def grade_peer():
            connections.close_all()
            try:
                grade_submission(submission_id, score=0, max_score=1, is_correct=False)
            finally:
                connections.close_all()

        def build_after_concurrent_commit(task):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(grade_peer).result(timeout=5)
            return build(task)

        with patch.object(comparisons, '_build_summary', side_effect=build_after_concurrent_commit):
            payload, signature, _ = comparisons._refresh_task_comparison(self.task.pk)
        self.assertEqual(signature, before)
        self.assertEqual(payload['students'][str(self.peer.pk)]['correct'], [1, 1])
        with analytics_read_transaction():
            self.assertNotEqual(signature, comparisons.comparison_source_signatures((self.task,))[self.task.pk])
