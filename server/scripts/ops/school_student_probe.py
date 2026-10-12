#!/usr/bin/env python3
"""Independent post-deploy DRF probe; roll back writes and emit metrics only."""
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


def probe(selection_path, *, course_id=2):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.conf import settings
    from django.core.exceptions import PermissionDenied
    from django.db import connection, transaction
    from django.db.models import F
    from django.test.utils import CaptureQueriesContext
    from django.utils import timezone
    from rest_framework.test import APIClient
    from apps.accounts.models import CourseEnrollment, User
    from apps.analytics.models import StudentExperimentResultSnapshot
    from apps.analytics.student_comparisons import shared_results_enabled
    from apps.analytics.student_result_snapshots import _authorize_result_access
    from apps.assessment.models import Attempt, ExperimentSession

    output = {'status': 'failed', 'database_status': 'rolled_back',
              'transaction_boundary_adapter': 'outer-repeatable-read-rollback',
              'request_transport_status': 'in_process_drf', 'requests': []}
    selection_path = Path(selection_path) if selection_path else None

    @contextmanager
    def inherited_read_transaction():
        # PostgreSQL cannot SET TRANSACTION in an already-used savepoint. The
        # outer probe supplies repeatable read; allow writes solely for rollback.
        with transaction.atomic():
            yield timezone.now()

    def eligible(session, question_id=None):
        enrollment = CourseEnrollment.objects.filter(
            course_id=course_id, student_id=session.student_id, is_active=True,
            class_group_id=session.class_task.class_group_id,
        ).select_related('student', 'course', 'class_group__student_analytics_policy').first()
        if enrollment is None:
            return None
        try:
            current, task, live_session, policy = _authorize_result_access(
                enrollment, session.class_task.experiment_id, at=timezone.now())
        except (LookupError, PermissionDenied):
            return None
        if live_session is None or live_session.pk != session.pk or not policy.show_item_results:
            return None
        attempts = Attempt.objects.filter(
            session=session, submission_event__isnull=False,
            assignment__snapshot__version_id=task.release_snapshot_id,
        )
        if question_id is not None:
            attempts = attempts.filter(assignment__snapshot_id=question_id)
        question_id = attempts.order_by('pk').values_list('assignment__snapshot_id', flat=True).first()
        return (current, task, question_id) if question_id is not None else None

    with transaction.atomic():
        if connection.vendor != 'postgresql':
            raise ValueError('PostgreSQL required')
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cursor.execute("SET LOCAL statement_timeout = '30000ms'")
        if not settings.STUDENT_LEARNING_ANALYTICS_ENABLED or not shared_results_enabled(course_id):
            output['status'] = 'shared_path_disabled'
            transaction.set_rollback(True)
            return output
        candidates = ExperimentSession.objects.filter(
            class_task__experiment__course_id=course_id,
            status=ExperimentSession.Status.COMPLETED,
            student__is_active=True, student__role=User.Role.STUDENT,
            participant_snapshot__student_id=F('student_id'),
            participant_snapshot__class_snapshot_id=F('class_task__class_snapshot_id'),
            participant_snapshot__version_id=F('class_task__release_snapshot_id'),
        ).select_related('class_task__experiment', 'class_task__release_snapshot', 'class_task__class_snapshot')
        selected = None
        if selection_path and selection_path.exists():
            stored = json.loads(selection_path.read_text())
            session = candidates.filter(pk=stored['session_id'], student_id=stored['student_id'], class_task_id=stored['task_id']).first()
            selected = eligible(session, stored['question_snapshot_id']) if session else None
        else:
            for session in candidates.order_by('pk').iterator(chunk_size=100):
                selected = eligible(session)
                if selected:
                    break
        if selected is None:
            output['status'] = 'no_eligible'
            transaction.set_rollback(True)
            return output
        enrollment, task, question_id = selected
        if selection_path and not selection_path.exists():
            descriptor = os.open(selection_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as target:
                json.dump({'session_id': session.pk, 'student_id': enrollment.student_id,
                           'task_id': task.pk, 'question_snapshot_id': question_id}, target)
            selection_path.chmod(0o600)
        client = APIClient()
        client.raise_request_exception = False
        client.force_login(enrollment.student)
        base = f'/api/v1/student/courses/{course_id}/learning/'
        detail = f'{base}experiments/{task.experiment_id}/'

        def request(label, path, etag=None):
            started = time.monotonic()
            row = {'route': label, 'status': None, 'elapsed_seconds': None,
                   'sql_count': 0, 'response_bytes': 0, 'own_item_count': None,
                   'cohort_json_sql_status': 'not_checked'}
            headers = {'HTTP_IF_NONE_MATCH': etag} if etag else {}
            response = None
            try:
                with transaction.atomic(), CaptureQueriesContext(connection) as queries:
                    response = client.get(path, secure=True, HTTP_HOST='ktedu.online', **headers)
                    row.update(status=response.status_code, sql_count=len(queries),
                               response_bytes=len(response.content))
                    if response.status_code == 200:
                        body = response.json()
                        if label == 'question':
                            row['own_item_count'] = 1
                        else:
                            items = body.get('item_results', body.get('items'))
                            row['own_item_count'] = len(items) if isinstance(items, list) else None
                        row['comparison_status'] = body.get('comparison_status')
                    cohort_json = any(
                        f'"{table}"."payload"' in query['sql']
                        for query in queries.captured_queries
                        for table in ('analytics_studentclasstaskcomparison',
                                      'analytics_studentclasslearningcomparison'))
                    row['cohort_json_sql_status'] = 'referenced' if cohort_json else 'absent'
            except Exception:
                row['status'] = 'exception'
            row['elapsed_seconds'] = round(time.monotonic()-started, 6)
            output['requests'].append(row)
            return response

        with patch('apps.analytics.student_result_snapshots.analytics_read_transaction', inherited_read_transaction), patch('apps.analytics.teaching.student_projection.analytics_read_transaction', inherited_read_transaction):
            # Clear only this disposable personal projection; the outer rollback
            # restores any pre-existing cache row along with all request writes.
            StudentExperimentResultSnapshot.objects.filter(class_task=task, student_id=enrollment.student_id).delete()
            cold = request('experiment_cold', detail)
            request('experiment_hot', detail)
            etag = cold.get('ETag') if cold is not None else None
            request('experiment_304', detail, etag)
            request('learning', base)
            request('question', f'{detail}questions/{question_id}/')
            request('task_results', f'/api/v1/student/tasks/{task.pk}/results/')
            # A conditional request must reauthorize, even with a matching ETag.
            with transaction.atomic():
                CourseEnrollment.objects.filter(pk=enrollment.pk).update(is_active=False)
                denied = request('experiment_304_revoked_enrollment', detail, etag)
                output['cache_304_auth_status'] = denied.status_code if denied is not None else 'exception'
                transaction.set_rollback(True)
        transaction.set_rollback(True)
    expected = [200, 200, 304, 200, 200, 200]
    passed = [row['status'] for row in output['requests'][:6]] == expected
    passed = passed and output['cache_304_auth_status'] in (403, 404)
    passed = passed and all(row['cohort_json_sql_status'] == 'absent' for row in output['requests'])
    passed = passed and output['requests'][0]['own_item_count'] > 0
    output['status'] = 'ok' if passed else 'failed'
    return output


if __name__ == '__main__':
    try:
        selection_path, = sys.argv[1:]
        if not selection_path.startswith('/tmp/school-student-probe-'):
            raise ValueError('Unexpected private selection path')
        result = probe(selection_path)
    except Exception:
        result = {'status': 'failed', 'database_status': 'rolled_back',
                  'transaction_boundary_adapter': 'outer-repeatable-read-rollback',
                  'request_transport_status': 'in_process_drf', 'requests': []}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'ok' else 2 if result['status'] == 'no_eligible' else 1)
