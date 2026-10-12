#!/usr/bin/env python3
"""Fixed isolated-school HTTP login and SQL evidence, only after formal acceptance."""
import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager, ExitStack
from pathlib import Path
from types import SimpleNamespace

RUN = '20261010T115729Z-358195'
PRIVATE = Path('/home/ktdata/school-cache-private') / RUN
PUBLIC = Path('/home/ktdata/school-cache-reports') / RUN
CANDIDATE = Path('/home/ktdata/school-cache-candidate')
BASE = 'http://127.0.0.1:18080'
SESSION_FILE = Path('/home/ktdata/school-school-uat-session.json')


def read(path):
    return json.loads(Path(path).read_text())


def write_json(path, body, mode):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(12) + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), mode) if hasattr(os, 'fchmod') else os.chmod(temporary, mode)
            json.dump(body, handle, sort_keys=True, indent=2)
        temporary.replace(path)
        os.chmod(path, mode)
    finally:
        temporary.unlink(missing_ok=True)


def require_formal(status, acceptance, config, binding):
    source, frontend = binding.get('candidate_archive_sha256'), binding.get('frontend_archive_sha256')
    if (status.get('run_id') != RUN or status.get('state') != 'passed'
            or acceptance.get('passed') is not True or not acceptance.get('checks')
            or any(value is not True for value in acceptance['checks'].values())
            or config.get('run_id') != RUN or config.get('source_sha256') != source
            or config.get('frontend_sha256') != frontend
            or not all(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) for value in (source, frontend))):
        raise ValueError('Same-source formal acceptance required')


def read_paths(fixture):
    course, experiment, task, question, classroom = (int(fixture[key]) for key in
        ('course_id', 'experiment_id', 'task_id', 'question_id', 'class_id'))
    prefix = f'/api/v1/teacher/courses/{course}/data/'
    query = urllib.parse.urlencode({'experiment_ids': experiment, 'class_id': classroom})
    return {
        'student': [
            ('experiment_results', f'/api/v1/student/courses/{course}/learning/experiments/{experiment}/'),
            ('results', f'/api/v1/student/tasks/{task}/results/'),
            ('result_status', f'/api/v1/student/tasks/{task}/results/?status_only=1'),
            ('one_question', f'/api/v1/student/courses/{course}/learning/experiments/{experiment}/questions/{question}/')],
        'teacher': [
            ('overview', prefix + 'overview/?' + query),
            ('preview_first', prefix + 'advanced/?' + query + '&table=sessions&page=1'),
            ('preview_status', prefix + 'advanced/status/?' + query + '&table=sessions'),
            ('preview_page2', prefix + 'advanced/?' + query + '&table=sessions&page=2'),
            ('response_page2', prefix + 'advanced/?' + query + '&table=responses&page=2')],
    }


def require_download(path, fixture):
    expected = rf'/analytics/courses/{int(fixture["course_id"])}/export-jobs/[a-f0-9]{{8}}-[a-f0-9]{{4}}-[a-f0-9]{{4}}-[a-f0-9]{{4}}-[a-f0-9]{{12}}/download/'
    if not isinstance(path, str) or re.fullmatch(expected, path) is None:
        raise ValueError('Unexpected authorized export download URL')
    return path


def safe_plan(plan):
    nodes = set()
    def visit(value):
        if isinstance(value, dict):
            node = value.get('Node Type')
            if isinstance(node, str) and re.fullmatch('[A-Za-z ]{1,60}', node):
                nodes.add(node)
            for key in ('Plan', 'Plans'):
                if key in value:
                    visit(value[key])
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(plan)
    return {'node_types': sorted(nodes)}


def safe_measurement(role, operation, path, response):
    return {'role': role, 'operation': operation, 'path': path, 'status': response.status,
            'elapsed_seconds': round(response.elapsed_seconds, 6), 'response_bytes': response.response_bytes}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPClient:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect())

    def request(self, path, method='GET', body=None):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Only fixed isolated candidate URLs allowed')
        headers = {'Accept': 'application/json', 'Referer': BASE + '/'}
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            headers['X-CSRFToken'] = next((cookie.value for cookie in self.cookies if cookie.name == 'csrftoken'), '')
            data = json.dumps(body).encode()
        request = urllib.request.Request(BASE + path, headers=headers, data=data, method=method)
        started = time.perf_counter()
        try:
            response = self.opener.open(request, timeout=35)
        except urllib.error.HTTPError as error:
            response = error
        content = bytearray()
        size = 0
        is_json = 'json' in response.headers.get('Content-Type', '')
        with response:
            while chunk := response.read(65536):
                size += len(chunk)
                if is_json:
                    if size > 4 * 1024**2:
                        raise ValueError('Unexpected verification JSON size')
                    content.extend(chunk)
        return SimpleNamespace(status=response.code, elapsed_seconds=time.perf_counter() - started,
            response_bytes=size, payload=json.loads(content) if is_json and content else None)

    def login(self, username, password, role):
        csrf = self.request('/api/v1/auth/csrf/')
        if csrf.status != 200:
            raise ValueError('Standard CSRF preparation failed')
        response = self.request('/api/v1/auth/login/', 'POST', {'username': username, 'password': password})
        if (response.status != 200 or (response.payload or {}).get('user', {}).get('role') != role
                or not any(cookie.name == 'sessionid' and cookie.value for cookie in self.cookies)):
            raise ValueError('Standard password session login failed')
        return response


def sql_probe(payload):
    """Cookie-authenticated GET replays; all exposure/audit writes are rolled back."""
    from django.db import connection, transaction
    from django.test import Client
    from django.test.utils import CaptureQueriesContext
    from django.core.signals import request_finished
    from django.db import close_old_connections
    from django.utils import timezone
    from unittest.mock import patch
    @contextmanager
    def inherited_read_transaction():
        with transaction.atomic():
            yield timezone.now()
    rows = []
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        database = cursor.fetchone()[0]
    if payload.get('expected_database') is not None and database != payload['expected_database']:
        raise ValueError('SQL replay requires isolated kt_e2e database')
    for item in payload['requests']:
        client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        for name, value in item['cookies'].items():
            client.cookies[name] = value
        summary = {'node_types': []}
        with transaction.atomic(), ExitStack() as adapters:
            for target in ('apps.analytics.student_result_snapshots', 'apps.analytics.student_result_status',
                           'apps.analytics.teaching.student_projection', 'apps.analytics.teaching.metrics',
                           'apps.analytics.teaching.drilldown', 'apps.analytics.teaching.scope'):
                adapters.enter_context(patch(target + '.analytics_read_transaction', inherited_read_transaction))
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cursor.execute("SET LOCAL statement_timeout = '30s'")
            started = time.perf_counter()
            with CaptureQueriesContext(connection) as queries:
                response = client.get(item['path'], HTTP_HOST='127.0.0.1')
                if response.streaming:
                    size = sum(len(chunk) for chunk in response.streaming_content)
                else:
                    size = len(response.content)
                # Client response cleanup otherwise closes CONN_MAX_AGE=0 inside
                # the rollback transaction before its captured reads can be planned.
                connected = request_finished.disconnect(close_old_connections)
                try:
                    response.close()
                finally:
                    if connected:
                        request_finished.connect(close_old_connections)
            elapsed = time.perf_counter() - started
            captured = list(queries.captured_queries)
            selects = [query for query in captured if query['sql'].lstrip().upper().startswith('SELECT ')]
            # Prefer actual domain reads over session authentication reads.
            business = [query for query in selects if re.search(r'"(?:analytics|assessment|experiments)_', query['sql'])]
            selected = sorted(business or selects, key=lambda query: float(query['time']), reverse=True)[:3]
            nodes = set()
            for query in selected:
                with connection.cursor() as cursor:
                    cursor.execute('EXPLAIN (FORMAT JSON) ' + query['sql'])
                    nodes.update(safe_plan(cursor.fetchone()[0])['node_types'])
            summary['node_types'] = sorted(nodes)
            transaction.set_rollback(True)
        row = safe_measurement(item['role'], item['operation'], item['path'],
            SimpleNamespace(status=response.status_code, elapsed_seconds=elapsed, response_bytes=size))
        row.update(query_count=len(captured), sql_seconds=round(sum(float(query['time']) for query in captured), 6),
                   maximum_query_seconds=round(max((float(query['time']) for query in captured), default=0), 6),
                   plan_summary=summary)
        rows.append(row)
    checks = {'authenticated_replays': bool(rows) and all(row['status'] == 200 for row in rows),
              'select_plan_observed': any(row['plan_summary']['node_types'] for row in rows),
              'transactions_rolled_back': bool(rows)}
    return {'checks': checks, 'passed': all(checks.values()), 'measurements': rows,
            'transaction_boundary_adapter': 'outer-repeatable-read-rollback',
            'request_transport': 'django_client_with_actual_http_session_cookie'}


def cookie_values(client):
    return {cookie.name: cookie.value for cookie in client.cookies if cookie.name in ('sessionid', 'csrftoken')}


class Verification:
    def __init__(self):
        self.stage = 'preflight'
        self.trusted = False
        self.rows = []
        self.checks = {}

    def publish(self, name, value):
        write_json(PUBLIC / name, value, 0o644)

    def progress(self, stage):
        self.stage = stage
        self.publish('post-formal-verification-progress.json', {'run_id': RUN, 'stage': stage, 'updated_at': time.time()})

    def prepare(self):
        if os.geteuid() != 0:
            raise ValueError('Root required to read private fixtures and publish protected sessions')
        for path in (PRIVATE / 'cached-source-binding.json', PRIVATE / 'fixtures' / (RUN + '-v6-mixed.json')):
            if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
                raise ValueError('Root-private fixture and binding required')
        self.binding = read(PRIVATE / 'cached-source-binding.json')
        self.config = read(PUBLIC / 'source-config.json')
        require_formal(read(PUBLIC / 'status.json'), read(PUBLIC / 'acceptance.json'), self.config, self.binding)
        self.fixture = read(PRIVATE / 'fixtures' / (RUN + '-v6-mixed.json'))
        if (self.fixture.get('database') != 'kt_e2e' or self.fixture.get('run_id') != RUN + '-v6-mixed'
                or len(self.fixture.get('usernames', [])) != 300 or len(self.fixture.get('teacher_usernames', [])) != 5
                or self.fixture.get('item_count') != 20):
            raise ValueError('Fixed retained mixed fixture required')
        self.bound = {'run_id': RUN, 'source_sha256': self.config['source_sha256'],
            'frontend_sha256': self.config['frontend_sha256'], 'environment': 'school_isolated_acceptance'}
        self.trusted = True
        self.progress('standard-password-logins')
        # Inspect only test web; do not copy environment values into public output.
        self.web = 'kt-school-e2e-web-1'
        inspected = json.loads(subprocess.check_output(['docker', 'inspect', self.web], text=True))[0]
        labels = inspected['Config'].get('Labels', {})
        if (labels.get('com.docker.compose.project') != 'kt-school-e2e'
                or labels.get('com.docker.compose.service') != 'web' or not inspected['State']['Running']
                or inspected['Image'] != self.config['roles']['web']['image_id']):
            raise ValueError('Same tested isolated web image required')

    def measure(self, client, role, operation, path, method='GET', body=None):
        response = client.request(path, method, body)
        self.rows.append(safe_measurement(role, operation, path, response))
        return response

    def http(self):
        clients = {'student': HTTPClient(), 'teacher': HTTPClient()}
        for role, key in (('student', 'usernames'), ('teacher', 'teacher_usernames')):
            response = clients[role].login(self.fixture[key][0], self.fixture['password'], role)
            self.rows.append(safe_measurement(role, 'password_login', '/api/v1/auth/login/', response))
            self.checks[role + '_standard_password_session'] = True
        paths = read_paths(self.fixture)
        replays = []
        self.progress('authenticated-business-reads')
        for role, values in paths.items():
            for index, (operation, path) in enumerate(values):
                if operation == 'one_question':
                    path = read_paths(self.fixture)['student'][-1][1]
                    values[index] = (operation, path)
                response = self.measure(clients[role], role, operation, path)
                self.checks[role + '_' + operation + '_http_200'] = response.status == 200
                if operation == 'results':
                    self.checks['completed_result_ready'] = bool(response.payload and response.payload.get('comparison_status') == 'ready'
                                                               and response.payload.get('completed_at'))
                if operation == 'experiment_results':
                    items = (response.payload or {}).get('item_results') or []
                    self.checks['own_completed_twenty_items'] = len(items) == 20
                    if not items or not isinstance(items[0].get('question_id'), int):
                        raise ValueError('Authorized own result did not expose a question snapshot')
                    self.fixture['question_id'] = items[0]['question_id']
                if operation in ('preview_first', 'preview_page2', 'response_page2'):
                    preview = (response.payload or {}).get('preview') or {}
                    page = preview.get('page') or {}
                    self.checks[operation + '_actual_page'] = bool(preview.get('rows')) and page.get('number') == (1 if operation == 'preview_first' else 2)
                replays.append({'role': role, 'operation': operation, 'path': path, 'cookies': cookie_values(clients[role])})
        prefix = f'/api/v1/teacher/courses/{self.fixture["course_id"]}/data/'
        self.progress('authorized-export-create-status-download')
        created = self.measure(clients['teacher'], 'teacher', 'export_create', prefix + 'export-jobs/', 'POST', {
            'package': 'experiment', 'confirm_identified': True,
            'scope': {'scope_version': 2, 'experiment_id': self.fixture['experiment_id'],
                      'class_mode': 'single', 'class_id': self.fixture['class_id']}})
        self.checks['export_create_http_201'] = created.status == 201
        job_id = (created.payload or {}).get('job_id', '')
        if not re.fullmatch('[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', str(job_id)):
            raise ValueError('Export creation did not return a valid job')
        path = prefix + 'export-jobs/' + job_id + '/'
        scheduled = (0, 5, 15, 30)
        started = time.monotonic()
        for elapsed in scheduled:
            time.sleep(max(0, started + elapsed - time.monotonic()))
            response = self.measure(clients['teacher'], 'teacher', 'export_status', path)
            state = (response.payload or {}).get('status')
            if response.status != 200 or state in ('succeeded', 'failed'):
                break
        self.checks['export_succeeded'] = response.status == 200 and state == 'succeeded'
        download = require_download((response.payload or {}).get('download_url'), self.fixture)
        fetched = self.measure(clients['teacher'], 'teacher', 'authorized_download', download)
        self.checks['authorized_download_nonempty'] = fetched.status == 200 and fetched.response_bytes > 0
        replays.extend({'role': 'teacher', 'operation': operation, 'path': target, 'cookies': cookie_values(clients['teacher'])}
                       for operation, target in (('export_status', path), ('authorized_download', download)))
        denied = clients['student'].request(paths['teacher'][0][1])
        self.checks['student_teacher_api_denied'] = denied.status == 403
        denied_download = clients['student'].request(download)
        self.checks['student_download_denied'] = denied_download.status in (302, 401, 403, 404)
        anonymous = HTTPClient()
        self.checks['anonymous_results_denied'] = anonymous.request(paths['student'][0][1]).status in (401, 403)
        self.checks['anonymous_download_denied'] = anonymous.request(download).status in (302, 401, 403, 404)
        self.checks['artifact_direct_paths_blocked'] = all(anonymous.request(path).status == 404
            for path in ('/media/exports/', '/media/research-assets/'))
        report = {**self.bound, 'auth_method': 'session_cookie', 'checks': self.checks,
                  'measurements': self.rows, 'passed': all(self.checks.values())}
        self.publish('auth-verification.json', report)
        if not report['passed']:
            raise ValueError('Authenticated business or artifact authorization checks failed')
        return clients, paths, replays

    def sql(self, replays):
        self.progress('same-session-cookie-sql-replays')
        payload = {'expected_database': 'kt_e2e', 'requests': replays}
        program = (
            "import os,json,sys;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
            "import django;django.setup();"
            "from scripts.ops.school_post_formal_verification import sql_probe;"
            "print(json.dumps(sql_probe(json.load(sys.stdin))))")
        completed = subprocess.run(['docker', 'exec', '-i', self.web, 'python', '-c', program],
            input=json.dumps(payload), text=True, capture_output=True, timeout=600)
        if completed.returncode:
            (PRIVATE / 'post-formal-sql-error.log').write_text(completed.stderr)
            os.chmod(PRIVATE / 'post-formal-sql-error.log', 0o600)
            raise ValueError('Cookie SQL replay failed; diagnostic private')
        report = json.loads(completed.stdout)
        self.publish('sql-verification.json', {**self.bound, **report})
        if not report.get('passed') or not report.get('checks') or not all(report['checks'].values()):
            raise ValueError('Cookie SQL replay or actual SELECT plan incomplete')

    def save_browser_sessions(self, clients, paths):
        import pwd
        self.progress('private-browser-session-handoff')
        sessions = {}
        for role, client in clients.items():
            cookies = [{'name': cookie.name, 'value': cookie.value, 'domain': '127.0.0.1', 'path': cookie.path,
                       'httpOnly': cookie.has_nonstandard_attr('HttpOnly'), 'secure': cookie.secure, 'sameSite': 'Lax'}
                       for cookie in client.cookies if cookie.name in ('sessionid', 'csrftoken')]
            if role == 'student':
                ui_path = f'/student/courses/{self.fixture["course_id"]}/learning/experiments/{self.fixture["experiment_id"]}/questions/{self.fixture["question_id"]}/'
            else:
                ui_path = f'/teacher/courses/{self.fixture["course_id"]}/data/advanced/?' + paths['teacher'][1][1].split('?', 1)[1]
            ui_paths = [ui_path]
            if role == 'teacher':
                ui_paths.append(f'/teacher/courses/{self.fixture["course_id"]}/data/exports/')
            else:
                ui_paths.insert(0, f'/student/courses/{self.fixture["course_id"]}/learning/experiments/{self.fixture["experiment_id"]}/')
            sessions[role] = {'cookies': cookies, 'paths': ui_paths,
                              'api_paths': [path for _, path in paths[role]]}
        value = {**self.bound, 'base_url': BASE, 'auth_method': 'standard_http_password_login', 'roles': sessions}
        path = PRIVATE / 'school-uat-session.json'
        write_json(path, value, 0o600)
        if SESSION_FILE.is_symlink():
            raise ValueError('Browser session handoff symlink refused')
        write_json(SESSION_FILE, value, 0o600)
        account = pwd.getpwnam('ktdata')
        os.chown(SESSION_FILE, account.pw_uid, account.pw_gid)

    def run(self):
        self.prepare()
        clients, paths, replays = self.http()
        self.sql(replays)
        self.save_browser_sessions(clients, paths)
        self.progress('http-auth-and-sql-passed-visual-pending')


def main():
    verifier = Verification()
    try:
        verifier.run()
    except Exception as error:
        if verifier.trusted:
            import ast
            messages = {node.args[0].value for node in ast.walk(ast.parse(Path(__file__).read_text()))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'ValueError'
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
            verifier.publish('post-formal-verification-failure.json', {
                'run_id': RUN, 'stage': verifier.stage, 'error_type': type(error).__name__,
                'reason': str(error) if type(error) is ValueError and str(error) in messages else type(error).__name__,
                'failed_checks': [key for key, passed in verifier.checks.items() if passed is not True],
                'raw_logs_private': True, 'production_changed': False})
        print('School HTTP/SQL verification failed: ' + type(error).__name__)
        raise SystemExit(1) from None
    print('School HTTP/SQL evidence written; visual acceptance remains separate')


if __name__ == '__main__':
    main()
