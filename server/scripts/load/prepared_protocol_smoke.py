"""Ten real HTTP clients verify admission and durable activation, not browser capacity."""
import argparse
import copy
import hashlib
import http.cookiejar
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


def new_navigation_material():
    wall_origin = datetime.now(timezone.utc)
    monotonic_origin = time.perf_counter()
    return {'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4()),
            'client_occurred_at': datetime.now(timezone.utc).isoformat(),
            'client_monotonic_ms': f'{(time.perf_counter() - monotonic_origin) * 1000:.3f}',
            'client_time_origin': wall_origin.isoformat(), 'reason': 'start'}


def receipt_identity(receipt):
    return {'session_id': receipt.get('session_id'), 'question_url': receipt.get('question_url'),
            'admission_execution_id': receipt.get('admission_execution_id'),
            'entry_receipt': copy.deepcopy(receipt.get('runtime', {}).get('entry_receipt'))}


class ProtocolFailure(RuntimeError):
    pass


class HttpClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = None
        self.calls = []

    def request(self, path, document=None):
        url = urllib.parse.urljoin(self.base_url + '/', path)
        if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(self.base_url).netloc:
            raise ProtocolFailure('response_endpoint_origin_changed')
        headers = {'Accept': 'application/json', 'Origin': self.base_url,
                   'Referer': self.base_url + '/student/', 'X-Request-ID': uuid.uuid4().hex}
        data = None
        if document is not None:
            data = json.dumps(document).encode()
            headers['Content-Type'] = 'application/json'
            headers['X-CSRFToken'] = self.csrf or ''
        started = time.perf_counter()
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                body = json.load(response)
                status = response.status
                retry = response.headers.get('Retry-After')
        except urllib.error.HTTPError as error:
            self.calls.append({'path': urllib.parse.urlsplit(url).path, 'status': error.code})
            error.close()
            raise ProtocolFailure(f'http_{error.code}') from None
        payload = body.get('data', body)
        self.calls.append({'path': urllib.parse.urlsplit(url).path, 'method': 'POST' if data else 'GET',
                           'status': status, 'duration_ms': (time.perf_counter() - started) * 1000})
        if payload.get('csrf_token'):
            self.csrf = payload['csrf_token']
        return payload, status, float(retry) if retry and retry.isdecimal() else 0

    def close(self):
        self.cookies.clear()


def run_client(account, base_url, ready, release):
    client = HttpClient(base_url)
    record = {'account_id': account['account_id'], 'user_id': account['user_id'],
              'task_id': account['taskId'], 'transport': 'protocol', 'status': 'failed',
              'scope': 'Admission/activation functional smoke; no browser operability measured.'}
    stage = 'csrf_login'
    try:
        client.request('/api/v1/auth/csrf/')
        client.request('/api/v1/auth/login/', {'username': account['username'], 'password': account['password']})
        client.request('/api/v1/auth/bootstrap/')
        material = new_navigation_material()
        record['client_material'] = material
        stage = 'shared_release'
        ready.set()
        if not release.wait(timeout=60):
            raise ProtocolFailure('shared_release_timeout')
        start_path = f"/api/v1/student/tasks/{account['taskId']}/start/"
        stage = 'start_request'
        response, status, retry_after = client.request(start_path, material)
        record['initial_status'] = response.get('status')
        record['initial_http_status'] = status
        token = response.get('admission_token')
        if not token or response.get('runtime'):
            raise ProtocolFailure('request_only_admission_contract_mismatch')
        expires = time.monotonic() + 60
        polls = 0
        while response.get('status') in ('queued', 'executing'):
            stage = 'admission_status'
            delay = max(retry_after, response.get('retry_after_seconds') or 0, min(10, 2 ** polls))
            record.setdefault('poll_delays_seconds', []).append(delay)
            time.sleep(delay)
            response, _, retry_after = client.request(response['poll_url'], {'admission_token': token, 'action': 'start'})
            polls += 1
            if time.monotonic() >= expires:
                raise ProtocolFailure('admission_timeout')
        if response.get('status') != 'admitted':
            raise ProtocolFailure('admission_not_granted')
        stage = 'start_claim'
        document = {**material, 'admission_token': token, 'admission_action': 'start'}
        activated, _, _ = client.request(start_path, document)
        record['receipt'] = receipt_identity(activated)
        if not record['receipt']['session_id'] or not record['receipt']['entry_receipt']:
            raise ProtocolFailure('activation_receipt_missing')
        stage = 'repeat_start_claim'
        repeated, _, _ = client.request(start_path, document)
        record['repeat_receipt'] = receipt_identity(repeated)
        if record['receipt'] != record['repeat_receipt']:
            raise ProtocolFailure('activation_receipt_identity_changed')
        record['status'] = 'completed'
        record['poll_count'] = polls
    except Exception as error:
        record['error_code'] = str(error) if isinstance(error, ProtocolFailure) else type(error).__name__
        record['failure_stage'] = stage
    finally:
        ready.set()
        record['http_calls'] = client.calls
        client.close()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accounts', required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--count', type=int, default=10)
    args = parser.parse_args()
    fixture = json.loads(Path(args.accounts).read_text(encoding='utf-8-sig'))
    if fixture.get('isolated_demo') is not True or urllib.parse.urlsplit(args.base_url).hostname not in ('127.0.0.1', 'localhost'):
        raise SystemExit('Explicit isolated local fixture required')
    if not 1 <= args.count <= 10:
        raise SystemExit('Functional smoke is bounded to ten clients')
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.conf import settings
    from django.db import connections
    from django.db.models import Count
    from apps.assessment.admission import AdmissionCoordinator
    from apps.assessment.models import AssessmentEvent, ExperimentSession, QuestionVisitEvent

    if settings.DATABASES['default']['NAME'] != fixture['database'] or not fixture['database'].endswith('_e2e'):
        raise SystemExit('Fixture/runtime isolated database binding differs')
    if settings.ASSESSMENT_ADMISSION_CAPACITY != 2 or settings.ASSESSMENT_ADMISSION_RATE_PER_SECOND != 64:
        raise SystemExit('This authorized smoke requires actual C2/R64')
    accounts = fixture['accounts'][:args.count]
    ids = [account['user_id'] for account in accounts]

    def database_snapshot():
        sessions = list(ExperimentSession.objects.filter(student_id__in=ids).values(
            'id', 'student_id', 'status', 'started_at', 'first_open_count', 'visit_event_count'))
        session_ids = [session['id'] for session in sessions]
        visits = {row['session_id']: row['count'] for row in QuestionVisitEvent.objects.filter(
            session_id__in=session_ids).values('session_id').annotate(count=Count('id'))}
        starts = {row['session_id']: row['count'] for row in AssessmentEvent.objects.filter(
            session_id__in=session_ids, event_type='session_started').values('session_id').annotate(count=Count('event_id'))}
        return [{**session, 'started_at': session['started_at'].isoformat() if session['started_at'] else None,
                 'visit_rows': visits.get(session['id'], 0), 'session_started_events': starts.get(session['id'], 0)} for session in sessions]

    before = database_snapshot()
    if len(before) != args.count or any(row['status'] != 'prepared' or row['started_at'] is not None or row['visit_rows'] for row in before):
        raise SystemExit('Smoke identities must all be fresh prepared sessions')
    coordinator = AdmissionCoordinator()
    occupancies = [{'phase': 'before', **coordinator.occupancy()}]
    stop = threading.Event()
    def sample_occupancy():
        while not stop.wait(.05):
            try:
                occupancies.append({'phase': 'during', **coordinator.occupancy()})
            except Exception:
                occupancies.append({'phase': 'during', 'error_code': 'occupancy_unavailable'})
    sampler = threading.Thread(target=sample_occupancy, daemon=True)
    sampler.start()
    release = threading.Event()
    readiness = [threading.Event() for _ in accounts]
    try:
        with ThreadPoolExecutor(max_workers=args.count) as pool:
            futures = [pool.submit(run_client, account, args.base_url, ready, release)
                       for account, ready in zip(accounts, readiness)]
            for ready in readiness:
                ready.wait(timeout=35)
            release.set()
            records = [future.result() for future in futures]
    finally:
        stop.set()
        sampler.join(timeout=2)
    occupancies.append({'phase': 'after', **coordinator.occupancy()})
    after = database_snapshot()
    checks = {'all_clients_completed': all(record['status'] == 'completed' for record in records),
              'one_start_and_visit_per_student': all(row['status'] == 'in_progress' and row['started_at'] is not None
                  and row['first_open_count'] == 1 and row['visit_event_count'] == 1 and row['visit_rows'] == 1
                  and row['session_started_events'] == 1 for row in after),
              'observed_occupancy_at_most_C2': all(row.get('occupied', 0) <= 2 for row in occupancies),
              'after_occupancy_zero': occupancies[-1]['occupied'] == 0}
    output = {'scope': 'Windows runserver + Node frontend gateway; no PgBouncer/Nginx worker or connection budget enforcement. Functional admission/receipt smoke only.',
              'capacity_verdict': None, 'C': 2, 'R': 64, 'planned_count': args.count,
              'fixture_sha256': hashlib.sha256(Path(args.accounts).read_bytes()).hexdigest(),
              'source_freeze': 'functional_smoke_not_formal_freeze', 'before_database': before, 'after_database': after,
              'occupancy_sampling_seconds': .05, 'occupancy': occupancies, 'checks': checks, 'clients': records}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    connections.close_all()
    coordinator.client.close()
    print(json.dumps({'planned': args.count, 'completed': sum(record['status'] == 'completed' for record in records), 'checks': checks}))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
