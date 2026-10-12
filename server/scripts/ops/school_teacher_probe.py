#!/usr/bin/env python3
"""Read existing teacher paths with rolled-back side effects; emit metrics only."""
import json
import os
import sys
import time
from pathlib import Path


def summarize_plan(document):
    root = document['Plan']
    nodes = set()
    indexes = set()

    def visit(node):
        nodes.add(node['Node Type'])
        if node.get('Index Name'):
            indexes.add(node['Index Name'])
        for child in node.get('Plans', ()):
            visit(child)

    visit(root)
    return {'node_types': sorted(nodes), 'index_names': sorted(indexes),
            'estimated_rows': root.get('Plan Rows'), 'total_cost': root.get('Total Cost')}


def probe(mode, selection_path):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.db import connection, transaction
    from django.db.models import Count
    from django.test.utils import CaptureQueriesContext
    from rest_framework.test import APIClient
    from apps.accounts.models import Course, CourseTeacher, User
    from apps.assessment.models import ExperimentSession

    selection_path = Path(selection_path)
    output = {'mode': mode, 'course_id': 2, 'authenticated': False,
              'database_side_effects_rolled_back': True, 'requests': [], 'passed': False}
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30000ms'")
        if mode == 'baseline':
            course = Course.objects.get(pk=2)
            teacher = course.owner if course.owner.role == User.Role.TEACHER else None
            if teacher is None:
                link = CourseTeacher.objects.filter(course=course, teacher__is_active=True).select_related('teacher').order_by('pk').first()
                teacher = link.teacher if link else None
            if teacher is None or not teacher.is_active:
                raise ValueError('No existing authorized teacher')
            selected = ExperimentSession.objects.filter(version__experiment__course_id=2).values('version__experiment_id').annotate(total=Count('id')).order_by('-total', 'version__experiment_id').first()
            if not selected or selected['total'] <= 20:
                raise ValueError('Need existing scope with more than20 sessions for page2')
            selection = {'teacher_id': teacher.pk, 'experiment_id': selected['version__experiment_id']}
            selection_path.write_text(json.dumps(selection))
            selection_path.chmod(0o600)
        else:
            selection = json.loads(selection_path.read_text())
            teacher = User.objects.get(pk=selection['teacher_id'], is_active=True)
        client = APIClient()
        client.raise_request_exception = False
        client.force_login(teacher)
        output['authenticated'] = True
        base = '/api/v1/teacher/courses/2/data/'
        routes = [('overview', base + 'overview/', {}),
                  ('advanced_first', base + 'advanced/', {'table': 'sessions', 'page': 1}),
                  ('advanced_page2', base + 'advanced/', {'table': 'sessions', 'page': 2})]
        for label, path, extra in routes:
            started = time.monotonic()
            row = {'route': label, 'status': None, 'elapsed_seconds': None, 'sql_count': 0, 'response_bytes': 0}
            try:
                with transaction.atomic(), CaptureQueriesContext(connection) as queries:
                    response = client.get(path, {'experiment_ids': selection['experiment_id'], **extra}, secure=True, HTTP_HOST='ktedu.online')
                    row.update(status=response.status_code, elapsed_seconds=round(time.monotonic()-started, 6),
                               sql_count=len(queries), response_bytes=len(response.content))
                    if label.startswith('advanced') and response.status_code == 200:
                        preview = response.json().get('preview') or {}
                        page = preview.get('page') or {}
                        # Only pagination counts/status; no rows/columns/names.
                        row['page_number'] = page.get('number')
                        row['page_size'] = page.get('size')
                        row['rows_returned'] = len(preview.get('rows') or [])
                        row['data_status'] = preview.get('data_status')
                    if mode == 'after':
                        plans = []
                        for query in queries.captured_queries:
                            statement = query['sql'].lstrip()
                            if not statement.upper().startswith('SELECT') or not any(table in statement for table in ('analytics_researchsession', 'assessment_experimentsession')):
                                continue
                            try:
                                with transaction.atomic(), connection.cursor() as cursor:
                                    cursor.execute('EXPLAIN (FORMAT JSON) ' + statement)
                                    document = cursor.fetchone()[0]
                                    plans.append(summarize_plan(document[0]))
                            except Exception:
                                plans.append({'explain_failed': True})
                            if len(plans) >= 8:
                                break
                        row['query_plan_summaries'] = plans
            except Exception as error:
                row.update(error_type=type(error).__name__, elapsed_seconds=round(time.monotonic()-started, 6))
            output['requests'].append(row)
        # force_login last_login/session writes, audit/enqueue changes and any
        # on_commit work are discarded. No real student result path is invoked.
        transaction.set_rollback(True)
    output['passed'] = all(row.get('status') == 200 for row in output['requests'])
    second = output['requests'][2]
    output['meaningful_page2'] = second.get('page_number') == 2 and second.get('rows_returned', 0) > 0
    if mode == 'after':
        output['passed'] = output['passed'] and output['meaningful_page2']
    return output


if __name__ == '__main__':
    try:
        mode, selection_path = sys.argv[1:]
        if mode not in ('baseline', 'after') or not selection_path.startswith('/tmp/school-teacher-probe-'):
            raise ValueError('Unexpected probe arguments')
        result = probe(mode, selection_path)
    except Exception as error:
        result = {'passed': False, 'error_type': type(error).__name__, 'database_side_effects_rolled_back': True}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
