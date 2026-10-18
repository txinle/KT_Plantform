"""PostgreSQL evidence from independent worker processes, including real crashes."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from .models import Attempt, ExperimentSession, GradeRecord, StudentQuestionAssignment, SubmissionEvent
from .session_submission_models import SessionSubmission
from .session_submission_services import accept_session_submission, claim_session_submission, run_session_submission
from . import test_session_submission as fixtures


# The gate starts competing operations only once both Django processes are ready.
WORKER = r"""
import json, sys, time, uuid
from types import SimpleNamespace
from unittest.mock import patch
import django
django.setup()
from django.db import connections
from django.utils import timezone
from apps.accounts.models import User
from apps.assessment.models import ExperimentSession
from apps.assessment.session_submission_services import accept_session_submission, claim_session_submission, run_session_submission
from apps.assessment.services import save_draft, _terminate_for_deadline
payload = json.loads(sys.stdin.readline())
print('READY', flush=True)
sys.stdin.readline()
try:
    mode = payload['mode']
    if mode.startswith('claim') or mode == 'mid_transaction':
        job = claim_session_submission(worker_id='process-' + str(__import__('os').getpid()))
        if mode in ('claim_wait', 'mid_transaction'):
            if mode == 'claim_wait':
                print('PAUSED', flush=True)
                time.sleep(60)
            else:
                def pause(*args):
                    print('PAUSED', flush=True)
                    time.sleep(60)
                with patch('apps.assessment.session_submission_services._finalize_session', side_effect=pause):
                    run_session_submission(job)
        elif payload.get('run') and job:
            run_session_submission(job)
        result = {'id': str(job.pk) if job else None}
    elif mode == 'run':
        claimed = SimpleNamespace(pk=uuid.UUID(payload['id']), session_id=payload['session_id'],
                                  lease_token=uuid.UUID(payload['lease_token']), attempts=payload['attempts'])
        result = {'status': run_session_submission(claimed).status}
    elif mode == 'accept':
        job = accept_session_submission(student=User.objects.get(pk=payload['student_id']),
            session_id=payload['session_id'], document=payload['document'])
        result = {'id': str(job.pk)}
    elif mode == 'save':
        attempt = save_draft(payload['attempt_id'], None, target_event_id=payload['event_id'], expected_version=1)
        result = {'version': attempt.response_version}
    elif mode == 'deadline':
        _terminate_for_deadline(ExperimentSession.objects.get(pk=payload['session_id']),
                                now=timezone.now(), reason='deadline_expired')
        result = {'deadline': True}
    elif mode == 'long_lived_command':
        from io import StringIO
        from apps.assessment.management.commands.run_session_submissions import Command
        class WorkerDone(Exception):
            pass
        idle_count = 0
        def wait_for_next_job(seconds):
            global idle_count
            idle_count += 1
            if idle_count > 1:
                raise WorkerDone()
            print('IDLE ' + str(connections['default'].connection.info.backend_pid), flush=True)
            sys.stdin.readline()
        try:
            with patch('apps.assessment.management.commands.run_session_submissions.time.sleep',
                       side_effect=wait_for_next_job):
                Command(stdout=StringIO()).handle(once=False, retry_failed=None, poll_seconds=0.1)
        except WorkerDone:
            result = {'recovered': True}
except Exception as error:
    result = {'error': type(error).__name__, 'code': getattr(error, 'code', '')}
finally:
    connections.close_all()
print(json.dumps(result), flush=True)
"""


@skipUnless(connection.vendor == 'postgresql', 'Independent process locking needs PostgreSQL')
class SessionSubmissionProcessTests(TransactionTestCase):
    setUp = fixtures.SessionSubmissionTests.setUp
    transition = fixtures.SessionSubmissionTests.transition
    ingest = fixtures.SessionSubmissionTests.ingest
    save_answer = fixtures.SessionSubmissionTests.save_answer
    document = fixtures.SessionSubmissionTests.document
    accept = fixtures.SessionSubmissionTests.accept

    def start_worker(self, mode, **payload):
        env = os.environ.copy()
        env.update(DJANGO_SETTINGS_MODULE='config.settings', POSTGRES_DB=connection.settings_dict['NAME'],
                   PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
        env.pop('POSTGRES_POOL_ALIAS', None)
        if mode == 'long_lived_command':
            env['DATABASE_CONN_MAX_AGE'] = '3600'
        process = subprocess.Popen([sys.executable, '-u', '-c', WORKER],
            cwd=Path(__file__).resolve().parents[2], env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        self.addCleanup(self.stop_worker, process)
        process.stdin.write(json.dumps({'mode': mode, **payload}) + '\n')
        process.stdin.flush()
        self.assertEqual(self.read_line(process), 'READY')
        return process

    @staticmethod
    def stop_worker(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)

    def read_line(self, process):
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        try:
            return lines.get(timeout=20).strip()
        except queue.Empty:
            process.terminate()
            _, errors = process.communicate(timeout=10)
            self.fail('Worker timed out: ' + errors)

    @staticmethod
    def release(*processes):
        for process in processes:
            process.stdin.write('GO\n')
            process.stdin.flush()

    def result(self, process):
        output, errors = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0, errors)
        return json.loads(output.strip())

    def another_receipt(self):
        student = User.objects.create_user(username='process-second-student', role='student')
        session = ExperimentSession.objects.create(student=student, version=self.version,
            capture_contract=self.capture_contract, assessment_flow_version=1)
        for old in self.session.assignments.select_related('snapshot', 'item_instance').all():
            assignment = StudentQuestionAssignment.objects.create(session=session, snapshot=old.snapshot,
                item_instance=old.item_instance, assigned_sequence_index=old.assigned_sequence_index)
            Attempt.objects.create(session=session, assignment=assignment)
        document = {'submission_token': str(uuid.uuid4()), 'expected_versions': [
            {'attempt_id': attempt.pk, 'response_version': 0, 'target_event_id': None}
            for attempt in session.attempts.order_by('pk')]}
        return accept_session_submission(student=student, session_id=session.pk, document=document)

    def expire_and_finish(self, job):
        SessionSubmission.objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        recovered = claim_session_submission(worker_id='recovery-process-parent')
        self.assertEqual(recovered.pk, job.pk)
        run_session_submission(recovered)
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(SubmissionEvent.objects.count(), 1)
        self.assertEqual(GradeRecord.objects.count(), 1)

    def test_independent_workers_claim_distinct_jobs(self):
        first = self.accept()
        second = self.another_receipt()
        workers = [self.start_worker('claim') for _ in range(2)]
        self.release(*workers)
        ids = {self.result(worker)['id'] for worker in workers}
        self.assertEqual(ids, {str(first.pk), str(second.pk)})

    def test_independent_workers_cannot_claim_one_receipt_twice(self):
        job = self.accept()
        workers = [self.start_worker('claim') for _ in range(2)]
        self.release(*workers)
        ids = [self.result(worker)['id'] for worker in workers]
        self.assertCountEqual(ids, [str(job.pk), None])
        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)

    def test_killed_process_after_claim_recovers_frozen_material(self):
        self.save_answer()
        job = self.accept()
        worker = self.start_worker('claim_wait')
        self.release(worker)
        self.assertEqual(self.read_line(worker), 'PAUSED')
        self.stop_worker(worker)
        self.assertFalse(SubmissionEvent.objects.exists())
        self.expire_and_finish(job)
        self.assertEqual(SubmissionEvent.objects.get().final_answer, 'A')

    def test_killed_process_mid_transaction_rolls_back_then_recovers_once(self):
        self.save_answer()
        job = self.accept()
        worker = self.start_worker('mid_transaction')
        self.release(worker)
        self.assertEqual(self.read_line(worker), 'PAUSED')
        self.assertFalse(SubmissionEvent.objects.exists())
        self.stop_worker(worker)
        self.assertFalse(SubmissionEvent.objects.exists())
        self.assertFalse(GradeRecord.objects.exists())
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'submitting')
        self.expire_and_finish(job)

    def test_duplicate_worker_execution_and_stale_fence_preserve_one_submission(self):
        self.save_answer()
        self.accept()
        stale = claim_session_submission(worker_id='stale')
        SessionSubmission.objects.filter(pk=stale.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        current = claim_session_submission(worker_id='current')
        def runner(job):
            return self.start_worker('run', id=str(job.pk), session_id=job.session_id,
                                     lease_token=str(job.lease_token), attempts=job.attempts)
        stale_worker = runner(stale)
        self.release(stale_worker)
        self.assertEqual(self.result(stale_worker)['status'], 'running')
        self.assertFalse(SubmissionEvent.objects.exists())
        workers = [runner(current), runner(current)]
        self.release(*workers)
        self.assertEqual([self.result(worker)['status'] for worker in workers], ['completed', 'completed'])
        self.assertEqual(SubmissionEvent.objects.count(), 1)
        self.assertEqual(GradeRecord.objects.count(), 1)

    def test_response_lost_after_commit_recovers_original_receipt(self):
        self.save_answer()
        job = self.accept()
        worker = self.start_worker('claim', run=True)
        self.release(worker)
        self.assertEqual(self.result(worker)['id'], str(job.pk))
        self.assertEqual(self.accept().pk, job.pk)
        self.assertEqual(SubmissionEvent.objects.count(), 1)
        self.assertEqual(self.session.submission_job.status, 'completed')

    def test_long_lived_command_recovers_idle_disconnect_without_duplicate_processing(self):
        from .session_submission_services import session_submission_worker_status
        self.save_answer()
        first = self.accept()
        worker = self.start_worker('long_lived_command')
        self.release(worker)
        idle = self.read_line(worker)
        self.assertTrue(idle.startswith('IDLE '), idle)
        self.assertEqual(session_submission_worker_status()['status'], 'connected')
        first.refresh_from_db()
        self.assertEqual(first.status, 'completed')
        second = self.another_receipt()
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_terminate_backend(%s)', [int(idle.split()[1])])
            self.assertTrue(cursor.fetchone()[0])
        self.release(worker)
        self.assertEqual(self.result(worker), {'recovered': True})
        self.assertEqual(session_submission_worker_status()['status'], 'not_connected')
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), ('completed', 'completed'))
        self.assertEqual((first.attempts, second.attempts), (1, 1))
        self.assertEqual(SubmissionEvent.objects.count(), 1)
        self.assertEqual(GradeRecord.objects.count(), 1)

    def test_save_competing_with_freeze_has_one_consistent_manifest(self):
        attempt = self.save_answer()
        document = self.document()
        changed = self.transition(attempt, 'A', 'B', parent=str(attempt.active_response_event_id), sequence=2)
        self.ingest([changed])
        accept = self.start_worker('accept', student_id=self.student.pk, session_id=self.session.pk, document=document)
        save = self.start_worker('save', attempt_id=attempt.pk, event_id=changed['event_id'])
        self.release(accept, save)
        accepted, saved = self.result(accept), self.result(save)
        if 'id' in accepted:
            self.assertIn('error', saved)
            claimed = claim_session_submission(worker_id='finish')
            run_session_submission(claimed)
            self.assertEqual(SubmissionEvent.objects.get().final_answer, 'A')
        else:
            self.assertEqual(accepted['code'], 'response_version_conflict')
            self.assertEqual(saved['version'], 2)
            self.assertFalse(SessionSubmission.objects.exists())
            attempt.refresh_from_db()
            self.assertEqual(attempt.current_response_snapshot.content, 'B')

    def test_deadline_competing_with_acceptance_reaches_one_effective_terminal_state(self):
        self.save_answer()
        accept = self.start_worker('accept', student_id=self.student.pk, session_id=self.session.pk,
                                   document=self.document())
        deadline = self.start_worker('deadline', session_id=self.session.pk)
        self.release(accept, deadline)
        accepted, terminated = self.result(accept), self.result(deadline)
        self.assertEqual(terminated, {'deadline': True})
        if 'id' in accepted:
            run_session_submission(claim_session_submission(worker_id='finish'))
            terminal = 'completed'
        else:
            self.assertEqual(accepted['code'], 'session_not_submittable')
            terminal = 'expired'
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, terminal)
        self.assertEqual(SubmissionEvent.objects.count(), 1)
        self.assertEqual(GradeRecord.objects.count(), 1)
