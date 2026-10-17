from apps.api.test_support import domain_payload
import os
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Event
from unittest.mock import patch

from django.core.management import call_command
from django.db import connections, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from apps.assessment.models import AssessmentEvent, ExperimentSession, SubmissionEvent
from apps.assessment.services import finish_session, record_visit, save_draft, submit_attempt
from apps.assessment.test_student_api import StudentApiFixtureMixin

from apps.api.management.commands.run_scheduler import Command


class SchoolSchedulerTests(SimpleTestCase):
    @patch("apps.api.management.commands.run_scheduler.call_command")
    def test_dedicated_export_worker_removes_exports_from_scheduler_cycle(self, call_command):
        with patch.dict(os.environ, {"RUN_SCHEDULER_EXPORT_JOBS": "0"}):
            Command().run_once()
        self.assertNotIn("run_export_jobs", [call.args[0] for call in call_command.call_args_list])
        self.assertEqual(len(call_command.call_args_list), 4)


class SchoolSchedulerFinishRaceTests(StudentApiFixtureMixin, TransactionTestCase):
    def test_expiry_rechecks_terminal_status_after_student_finish_commits(self):
        self.assertEqual(connections['default'].vendor, 'postgresql')
        # Retain the expiry/finish race for historical records through domain services.
        from apps.assessment.services import get_or_create_session
        session = get_or_create_session(self.experiment, self.student, class_task=self.task)
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, 'B')
        ExperimentSession.objects.filter(pk=session.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        before_row_lock = Event()
        permit_row_lock = Event()

        def expire():
            connections.close_all()
            output = StringIO()
            def observe_lock(execute, sql, params, many, context):
                if 'FOR UPDATE' in sql and 'assessment_experimentsession' in sql:
                    before_row_lock.set()
                    if not permit_row_lock.wait(timeout=10):
                        raise TimeoutError('Student finish did not commit')
                return execute(sql, params, many, context)
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                with connections['default'].execute_wrapper(observe_lock):
                    call_command('expire_due_sessions', stdout=output)
                return output.getvalue().strip()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                with transaction.atomic():
                    ExperimentSession.objects.select_for_update().get(pk=session.pk)
                    ExperimentSession.objects.filter(pk=session.pk).update(deadline_at=timezone.now() + timedelta(minutes=10))
                    future = pool.submit(expire)
                    self.assertTrue(before_row_lock.wait(timeout=5), 'Expiry did not enumerate the previously due session')
                    submit_attempt(attempt.pk, uuid.uuid4())
                    finish_session(session.pk)
            finally:
                permit_row_lock.set()
            self.assertEqual(future.result(timeout=15), '0')
        session.refresh_from_db()
        self.assertEqual(session.status, ExperimentSession.Status.COMPLETED)
        submission = SubmissionEvent.objects.get(attempt=attempt)
        self.assertEqual(submission.final_answer, 'B')
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type='response_committed').count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='session_completed').count(), 1)
