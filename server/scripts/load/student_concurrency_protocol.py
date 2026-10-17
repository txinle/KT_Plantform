"""Same-DUT HTTP protocol workload; never evidence of browser operability.

Use a fresh isolated seed_prepared_startup_load fixture per cell. Outputs contain
synthetic answers and acknowledged capture facts, but never credentials or leases.
Independent database/resource reconciliation and real page witnesses are required.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from capture_protocol import BrowserAlignedStream
from prepared_protocol_smoke import HttpClient as CookieClient, ProtocolFailure, new_navigation_material
from student_concurrency import digest, select_roster, _stats

VERSION = 'student-concurrency-protocol-v2'
MODES = ('login', 'startup', 'login-mixed', 'startup-mixed', 'full-flow', 'submission')
THRESHOLDS = {'login_p95_ms': 10000, 'login_p99_ms': 20000, 'startup_p95_ms': 5000,
              'startup_p99_ms': 10000, 'save_p95_ms': 1000, 'save_p99_ms': 3000,
              'save_scheduled_p95_ms': 1000, 'save_scheduled_p99_ms': 3000,
              'submission_p95_ms': 1000, 'submission_p99_ms': 3000, 'completion_max_ms': 300000}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def origin(base_url):
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Require the isolated same-DUT loopback HTTP origin')
    return base_url.rstrip('/')


def values(kind):
    return {'single_choice': ('A', 'B'), 'multiple_choice': (['A', 'B'], ['B']),
            'true_false': ('true', 'false'), 'numeric': ('4', '5'),
            'short_answer': ('concurrency response', 'modified concurrency response'),
            'fill_blank': ('四', '五')}[kind]


def empty_value(kind):
    return [] if kind == 'multiple_choice' else ('' if kind in ('numeric', 'short_answer', 'fill_blank') else None)


def build_plan(fixture, *, mode, count, run_id, fixture_sha256, base_url):
    target = origin(base_url)
    if mode not in MODES:
        raise ValueError('Unknown protocol mode')
    if (fixture.get('isolated_demo') is not True or fixture.get('database') != 'kt_e2e'
            or fixture.get('source_ownership', {}).get('command') != 'seed_prepared_startup_load'
            or fixture.get('run_id') != run_id or digest(fixture['fixture_spec']) != fixture['plan_sha']):
        raise ValueError('Require the unchanged isolated fixture and matching run identity')
    selected = select_roster(fixture['accounts'], 'login' if mode == 'startup' else mode, count)
    roster = []
    for account, role in selected:
        if mode in ('startup', 'full-flow'):
            role = mode
        questions = account['questions'][:2]
        if len(questions) != 2:
            raise ValueError('Two frozen question bindings required')
        schedule = []
        if role == 'save':
            schedule = [{'assignment_id': questions[i % 2]['assignment_id'],
                         'value': copy.deepcopy(values(questions[i % 2]['question_type'])[(i // 2) % 2])}
                        for i in range(15)]
        elif role in ('full-flow', 'submission'):
            schedule = [{'assignment_id': questions[0]['assignment_id'], 'value': copy.deepcopy(values(questions[0]['question_type'])[0])},
                        {'assignment_id': questions[0]['assignment_id'], 'value': copy.deepcopy(values(questions[0]['question_type'])[1])},
                        {'assignment_id': questions[1]['assignment_id'], 'value': copy.deepcopy(values(questions[1]['question_type'])[0])}]
        final = {action['assignment_id']: action['value'] for action in schedule}
        roster.append({'account_id': account['account_id'], 'user_id': account['user_id'],
                       'course_id': account['courseId'], 'task_id': account['taskId'],
                       'session_id': account['session_id'], 'role': role,
                       'answers': [{'assignment_id': q['assignment_id'], 'attempt_id': q['attempt_id'],
                                    'question_type': q['question_type'], 'value': final.get(q['assignment_id']),
                                    'confidence_probability': None} for q in questions], 'save_schedule': schedule})
    plan = {'version': VERSION, 'run_id': run_id, 'mode': mode, 'count': count, 'roster': roster,
            'fixture_sha256': fixture_sha256, 'fixture_spec_sha256': fixture['plan_sha'],
            'target_origin': target, 'same_DUT': True, 'transport': 'protocol',
            'browser_operability_measured': False, 'preparation_login_concurrency': 4,
            'save_rounds': 15, 'save_interval_ms': 2000,
            'operation_timeout_ms': 60000, 'login_timeout_ms': 30000, 'completion_timeout_ms': 300000,
            'maximum_release_skew_ms': 1000, 'thresholds': THRESHOLDS,
            'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return {**plan, 'plan_id': digest(plan)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpClient(CookieClient):
    def __init__(self, base_url):
        super().__init__(origin(base_url))
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect())

    def _request(self, path, document=None, *, deadline=None, raw=False):
        url = urllib.parse.urljoin(self.base_url + '/', path)
        if urllib.parse.urlsplit(url)[:2] != urllib.parse.urlsplit(self.base_url)[:2]:
            raise ProtocolFailure('response_endpoint_origin_changed')
        remaining = deadline - time.monotonic() if deadline is not None else 30
        if remaining <= 0:
            raise ProtocolFailure('operation_timeout')
        request_id = uuid.uuid4().hex
        headers = {'Accept': 'application/octet-stream' if raw else 'application/json',
                   'Origin': self.base_url, 'Referer': self.base_url + '/student/', 'X-Request-ID': request_id}
        data = None
        if document is not None:
            data = json.dumps(document, ensure_ascii=False, allow_nan=False).encode()
            headers.update({'Content-Type': 'application/json', 'X-CSRFToken': self.csrf or ''})
        started, status, code = time.monotonic(), None, None
        try:
            try:
                response = self.opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=min(30, remaining))
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status, retry = response.code, response.headers.get('Retry-After')
                body = response.read(32 * 1024 * 1024 + 1)
            if len(body) > 32 * 1024 * 1024:
                raise ProtocolFailure('response_size_limit')
            if raw:
                if status != 200:
                    raise ProtocolFailure(f'http_{status}')
                return body
            try:
                body = json.loads(body)
            except (ValueError, UnicodeError):
                raise ProtocolFailure('non_json_response') from None
            payload = body.get('data', body) if isinstance(body, dict) else body
            if not isinstance(payload, dict):
                # Task-list endpoint may legitimately return a JSON list.
                if isinstance(payload, list):
                    payload = {'results': payload}
                else:
                    raise ProtocolFailure('invalid_response_envelope')
            code = (payload.get('error') or {}).get('code') or payload.get('code')
            if payload.get('csrf_token'):
                self.csrf = payload['csrf_token']
            try:
                if retry is not None and retry.isascii() and retry.isdigit():
                    retry = float(retry)
                elif retry is not None and len(retry) > 5 and retry[:3].isalpha() and retry[3:5] == ', ':
                    retry = max(0, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                else:
                    retry = None
                if retry is not None and not math.isfinite(retry):
                    retry = None
            except (ValueError, TypeError, OverflowError):
                retry = None
            return payload, status, retry
        except (urllib.error.URLError, TimeoutError, OSError):
            code = 'transport_error'
            raise ProtocolFailure(code) from None
        finally:
            self.calls.append({'path': urllib.parse.urlsplit(url).path, 'method': 'POST' if data is not None else 'GET',
                               'status': status, 'duration_ms': (time.monotonic() - started) * 1000,
                               'http_send_monotonic_ms': started * 1000,
                               'request_id': request_id, 'error_code': code})

    def request(self, path, document=None, **kwargs):
        return self._request(path, document, **kwargs)

    def get_bytes(self, path, **kwargs):
        return self._request(path, raw=True, **kwargs)


def checked(client, path, document=None, *, allowed=(200,), deadline=None):
    payload, status, _ = client.request(path, document, deadline=deadline)
    if status not in allowed:
        raise ProtocolFailure(str(payload.get('error', {}).get('code') or payload.get('code') or f'http_{status}'))
    return payload


def activate(client, task_id, material, *, clock=time.monotonic, sleep=time.sleep, jitter=random.uniform):
    deadline, tries = clock() + 60, 0
    while clock() < deadline:
        payload, status, retry = client.request(f'/api/v1/student/tasks/{task_id}/start/', material, deadline=deadline)
        if status in (200, 201):
            return payload
        if status not in (202, 429) or (status == 429 and payload.get('error', {}).get('code') != 'START_BUSY'):
            raise ProtocolFailure(str(payload.get('error', {}).get('code') or f'http_{status}'))
        delay = max(retry or 0, payload.get('retry_after_seconds') or 0) + jitter(0, min(4, 2 ** min(tries, 2)))
        sleep(min(delay, max(0, deadline - clock())))
        tries += 1
    raise ProtocolFailure('activation_timeout')


class ProtocolCapture(BrowserAlignedStream):
    def event(self, *args, **kwargs):
        row = super().event(*args, **kwargs)
        row.update(app_version=VERSION, quality_flags=['clock_unsynchronized'])
        self.events[row['client_sequence_no']] = copy.deepcopy(row)
        return row

    def checkpoint(self, trigger):
        row = super().checkpoint(trigger)
        row.update(app_version=VERSION, quality_flags=['clock_unsynchronized'])
        self.checkpoints[row['checkpoint_id']] = copy.deepcopy(row)
        return row


class Actor:
    def __init__(self, account, planned, plan, client):
        self.account, self.planned, self.plan, self.client = account, planned, plan, client
        identity = (f'prepared-fixture:{plan["run_id"]}:{account["user_id"]}' if account['initial_state'] == 'active'
                    else f'{plan["plan_id"]}:{account["account_id"]}')
        self.client_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        self.material = new_navigation_material()
        self.material['client_instance_id'] = self.client_id
        if account['initial_state'] == 'active':
            self.material['navigation_id'] = self.client_id
        self.stream = ProtocolCapture(account['session_id'], self.client_id)
        self.dto, self.lease, self.lease_expires_at = None, None, None
        self.expected = {q['attempt_id']: {'attempt_id': q['attempt_id'], 'assignment_id': q['assignment_id'],
            'response_version': 0, 'target_event_id': None, 'answer': empty_value(q['question_type']),
            'confidence_probability': None, 'transition_event_ids': []} for q in account['questions']}
        self.record = {key: planned[key] for key in ('account_id', 'user_id', 'task_id', 'session_id', 'course_id', 'role')}
        self.record.update(plan_id=plan['plan_id'], run_id=plan['run_id'], client_instance_id=self.client_id,
            transport='protocol', same_DUT=True, status='failed', capture_events=[], capture_checkpoints=[],
            event_acknowledgements=[], checkpoint_acknowledgements=[], visits=[],
            lease_acquisitions=[], lease_releases=[], login_attempts=[], login_busy_responses=0,
            save_samples=[{'schedule_index': index, 'assignment_id': action['assignment_id'],
                'status': 'not_executed', 'planned_monotonic_ms': None, 'action_start_monotonic_ms': None,
                'http_send_monotonic_ms': None, 'ack_monotonic_ms': None, 'client_backlog_ms': None,
                'duration_ms': None, 'http_ms': None, 'action_ms': None, 'scheduled_ms': None}
                for index, action in enumerate(planned['save_schedule'])])
        self.operation_index = 0

    def csrf(self):
        began = time.monotonic()
        checked(self.client, '/api/v1/auth/csrf/')
        if not self.client.csrf:
            raise ProtocolFailure('csrf_token_missing')
        self.record['csrf_preparation_ms'] = (time.monotonic() - began) * 1000

    def login(self):
        began = time.monotonic()
        deadline, retries = began + 30, 0
        credentials = {'username': self.account['username'], 'password': self.account['password']}
        try:
            while time.monotonic() < deadline:
                attempt = {'attempt': len(self.record['login_attempts']) + 1, 'status': None, 'error_code': None}
                self.record['login_attempts'].append(attempt)
                started = time.monotonic()
                try:
                    payload, status, retry = self.client.request('/api/v1/auth/login/', credentials, deadline=deadline)
                    code = payload.get('error', {}).get('code') or payload.get('code')
                    attempt.update(status=status, error_code=code)
                except ProtocolFailure as error:
                    attempt['error_code'] = str(error)
                    raise
                finally:
                    attempt['duration_ms'] = (time.monotonic() - started) * 1000
                    attempt['elapsed_ms'] = (time.monotonic() - began) * 1000
                if status == 429 and code == 'LOGIN_BUSY':
                    self.record['login_busy_responses'] += 1
                if time.monotonic() >= deadline:
                    raise ProtocolFailure('login_timeout')
                if status == 200:
                    break
                if status != 429 or code != 'LOGIN_BUSY':
                    raise ProtocolFailure(str(code or f'http_{status}'))
                delay = (retry if retry is not None else 1) + random.uniform(0, min(2, .25 * 2 ** min(retries, 3)))
                time.sleep(min(delay, max(0, deadline - time.monotonic())))
                retries += 1
            else:
                raise ProtocolFailure('login_timeout')
            tasks = checked(self.client, f'/api/v1/student/courses/{self.account["courseId"]}/tasks/', deadline=deadline)
            if time.monotonic() >= deadline:
                raise ProtocolFailure('login_timeout')
        finally:
            self.record['login_total_ms'] = (time.monotonic() - began) * 1000
        def contains(value):
            if isinstance(value, list):
                return any(contains(item) for item in value)
            if isinstance(value, dict):
                return any(value.get(key) == self.account['taskId'] for key in ('id', 'task_id')) or any(contains(v) for v in value.values() if isinstance(v, (dict, list)))
            return False
        if not contains(tasks):
            raise ProtocolFailure('task_list_binding_missing')

    def content(self, descriptor, deadline):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = self.client.get_bytes(descriptor['url'], deadline=deadline)
        if len(raw) != descriptor['byte_length'] or hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
            raise ProtocolFailure('prepared_content_digest_mismatch')
        envelope = json.loads(raw)
        if envelope.get('version') != 1 or envelope.get('compression') != 'gzip':
            raise ProtocolFailure('prepared_content_envelope_mismatch')
        decoded = AESGCM(base64.b64decode(descriptor['key'])).decrypt(base64.b64decode(envelope['nonce']), base64.b64decode(envelope['ciphertext']), None)
        return json.loads(gzip.decompress(decoded))

    def install(self, dto, *, deadline):
        if dto.get('entry_allowed') is False:
            raise ProtocolFailure(str(dto.get('entry_denial_code') or 'entry_not_allowed'))
        self.dto = dto
        aid = dto['assignment']['attempt_id']
        if aid not in self.expected:
            raise ProtocolFailure('runtime_attempt_binding_mismatch')
        receipt = dto.get('entry_receipt')
        if not receipt or receipt.get('session_id') != self.account['session_id']:
            raise ProtocolFailure('entry_receipt_binding_mismatch')
        self.record['visits'].append(copy.deepcopy(receipt))
        process, expected = dto['process'], self.expected[aid]
        if process.get('response_version', 0) != expected['response_version'] or process.get('active_response_event_id') != expected['target_event_id']:
            raise ProtocolFailure('restored_head_mismatch')
        if expected['response_version'] and dto.get('draft_answer') != expected['answer']:
            raise ProtocolFailure('restored_answer_mismatch')
        paper = dto.get('prepared_paper')
        if paper:
            question = self.content(paper['question'], deadline)
            if question['question'].get('snapshot_id') != dto['question']['snapshot_id']:
                raise ProtocolFailure('question_snapshot_mismatch')
            self.dto['question'] = question['question']
        lease = dto.get('initial_writer_lease')
        if lease:
            if lease.get('writer_client_instance_id') != self.client_id:
                raise ProtocolFailure('initial_lease_client_mismatch')
            self.lease = {key: lease[key] for key in ('writer_lease_token', 'writer_client_instance_id')}
            self.lease_expires_at = lease['expires_at']
            self.lease_fact('acquisitions')

    def startup(self):
        deadline = time.monotonic() + 60
        receipt = activate(self.client, self.account['taskId'], self.material)
        if receipt.get('session_id') != self.account['session_id']:
            raise ProtocolFailure('activation_session_binding_mismatch')
        self.record['activation_receipt'] = {'session_id': receipt['session_id'], 'task_id': self.account['taskId'],
                                            'entry_receipt': copy.deepcopy(receipt['runtime'].get('entry_receipt'))}
        self.install(receipt['runtime'], deadline=deadline)

    def manifest(self):
        paper = self.dto.get('prepared_paper')
        if not paper:
            raise ProtocolFailure('prepared_manifest_missing')
        manifest = self.content(paper['manifest'], time.monotonic() + 60)
        if manifest.get('session_id') != self.account['session_id'] or manifest.get('question_count') != len(self.expected):
            raise ProtocolFailure('manifest_session_binding_mismatch')
        bindings = {(q['attempt_id'], q['assignment_id']) for q in manifest['navigation']}
        if bindings != {(q['attempt_id'], q['assignment_id']) for q in self.expected.values()}:
            raise ProtocolFailure('manifest_attempt_binding_mismatch')
        self.record['manifest_verified'] = True

    def lease_fact(self, kind):
        self.operation_index += 1
        self.record[f'lease_{kind}'].append({'attempt_id': self.dto['assignment']['attempt_id'],
            'client_instance_id': self.client_id, 'confirmed': True, 'expires_at': self.lease_expires_at,
            'operation_index': self.operation_index})

    def ensure_lease(self):
        expires = datetime.fromisoformat(self.lease_expires_at.replace('Z', '+00:00')) if self.lease_expires_at else None
        if self.lease and expires and (expires - datetime.now(timezone.utc)).total_seconds() > 5:
            return
        if self.lease and expires and expires > datetime.now(timezone.utc):
            payload = checked(self.client, self.dto['process']['writer_lease_renew_url'],
                {'client_instance_id': self.client_id, 'lease_token': self.lease['writer_lease_token']})
        else:
            payload = checked(self.client, self.dto['process']['writer_lease_acquire_url'], {'client_instance_id': self.client_id}, allowed=(200, 201))
        self.lease = {'writer_lease_token': payload['lease_token'], 'writer_client_instance_id': self.client_id}
        self.lease_expires_at = payload['expires_at']
        self.lease_fact('acquisitions')

    def release_lease(self):
        if self.lease:
            checked(self.client, self.dto['process']['writer_lease_release_url'],
                    {'client_instance_id': self.client_id, 'lease_token': self.lease['writer_lease_token']})
            self.lease_fact('releases')
            self.lease, self.lease_expires_at = None, None

    def enter(self, assignment_id):
        if self.dto['assignment'].get('assignment_id') == assignment_id:
            return
        self.release_lease()
        material = new_navigation_material()
        material.pop('client_instance_id')
        material['reason'] = 'navigation_click'
        dto = checked(self.client, f'/api/v1/student/sessions/{self.account["session_id"]}/questions/{assignment_id}/', material)
        self.install(dto, deadline=time.monotonic() + 60)

    def save(self, action):
        started = time.monotonic()
        sample = next(row for row in self.record['save_samples'] if row['status'] == 'not_executed')
        planned = sample['planned_monotonic_ms']
        if planned is None:
            planned = sample['planned_monotonic_ms'] = started * 1000
        sample.update(status='executing', action_start_monotonic_ms=started * 1000,
                      client_backlog_ms=max(0, started * 1000 - planned))
        try:
            self._save(action, sample)
        except Exception:
            sample['status'] = 'failed'
            raise
        finally:
            sample['action_ms'] = (time.monotonic() - started) * 1000

    def _save(self, action, sample):
        if self.dto['assignment']['assignment_id'] != action['assignment_id']:
            self.enter(action['assignment_id'])
        self.ensure_lease()
        aid = self.dto['assignment']['attempt_id']
        expected = self.expected[aid]
        if expected['assignment_id'] != action['assignment_id']:
            raise ProtocolFailure('save_assignment_binding_mismatch')
        event = self.stream.transitions(aid, self.dto['question']['question_type'], expected['answer'], expected['target_event_id'], [action['value']])[0]
        checkpoint = self.stream.checkpoint('ack_progress')
        events = self.stream.pending_events()
        self.record['capture_events'].extend(copy.deepcopy(events))
        self.record['capture_checkpoints'].append(copy.deepcopy(checkpoint))
        document = {
            'answer': action['value'], 'target_event_id': event['event_id'], 'expected_version': expected['response_version'],
            'client_occurred_at': event['client_wall_time_utc'], **self.lease,
            'capture_operation_id': str(uuid.uuid4()), 'inline_confidence': {'probability': None, 'answer_event_id': event['event_id']},
            'capture_bundle': {'version': 1, 'client_instance_id': self.client_id, 'event_boundary': self.stream.sequence,
                               'events': events, 'checkpoints': [checkpoint]}}
        sample['http_send_monotonic_ms'] = time.monotonic() * 1000
        call_count = len(self.client.calls)
        try:
            payload = checked(self.client, self.dto['actions']['save_draft_url'], document)
        finally:
            if len(self.client.calls) > call_count:
                call = self.client.calls[-1]
                if call.get('path') != urllib.parse.urlsplit(self.dto['actions']['save_draft_url']).path:
                    raise ProtocolFailure('save_http_timing_binding_mismatch')
                sample.update(http_ms=call['duration_ms'],
                              http_send_monotonic_ms=call.get('http_send_monotonic_ms', sample['http_send_monotonic_ms']))
        if (payload.get('committed') is not True or payload.get('response_version') != expected['response_version'] + 1
                or payload.get('active_response_event_id') != event['event_id']):
            raise ProtocolFailure('save_head_not_acknowledged')
        acknowledgements = payload.get('acknowledgements', [])
        self.stream.accept_acknowledgements(acknowledgements)
        if event['client_sequence_no'] not in self.stream.acked or checkpoint['checkpoint_id'] not in payload.get('checkpoint_ids', []):
            raise ProtocolFailure('save_capture_not_acknowledged')
        if payload.get('inline_confidence', {}).get('probability') is not None:
            raise ProtocolFailure('optional_confidence_changed')
        self.record['event_acknowledgements'].extend(copy.deepcopy(acknowledgements))
        self.record['checkpoint_acknowledgements'].extend(payload['checkpoint_ids'])
        expected.update(response_version=payload['response_version'], target_event_id=event['event_id'], answer=copy.deepcopy(action['value']))
        expected['transition_event_ids'].append(event['event_id'])
        acknowledged = time.monotonic() * 1000
        sample.update(status='acknowledged', ack_monotonic_ms=acknowledged,
                      scheduled_ms=acknowledged - sample['planned_monotonic_ms'], duration_ms=sample['http_ms'],
                      response_version=expected['response_version'], target_event_id=event['event_id'])

    def final_checkpoint(self):
        if self.stream.pending_events():
            raise ProtocolFailure('capture_events_still_pending')
        checkpoint = self.stream.checkpoint('ack_progress')
        self.record['capture_checkpoints'].append(copy.deepcopy(checkpoint))
        payload = checked(self.client, self.dto['process']['checkpoint_url'], checkpoint, allowed=(200, 201))
        if payload.get('checkpoint_id') != checkpoint['checkpoint_id']:
            raise ProtocolFailure('checkpoint_identity_changed')
        self.record['checkpoint_acknowledgements'].append(payload['checkpoint_id'])

    def submit(self, *, clock=time.monotonic, sleep=time.sleep):
        began, deadline = clock(), clock() + 300
        token = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{self.plan["plan_id"]}:{self.account["account_id"]}:submission'))
        self.record['submission_token'] = token
        payload = checked(self.client, f'/api/v1/student/sessions/{self.account["session_id"]}/finish/', {
            'submission_token': token, 'client_clicked_at': utcnow(),
            'expected_versions': [{key: row[key] for key in ('attempt_id', 'response_version', 'target_event_id')}
                                  for row in self.expected.values()]}, allowed=(200, 202), deadline=deadline)
        self.record['submission_ms'] = (clock() - began) * 1000
        self.record['submission_id'] = payload['submission_id']
        status_url = payload.get('status_url')
        polls = 0
        while payload.get('status') in ('queued', 'running') and clock() < deadline:
            sleep(min(10, 2 ** min(polls + 1, 4), max(0, deadline - clock())))
            if clock() >= deadline:
                break
            payload = checked(self.client, status_url, deadline=deadline)
            if str(payload.get('submission_id')) != str(self.record['submission_id']):
                raise ProtocolFailure('submission_identity_changed')
            polls += 1
        self.record['final_status'] = payload.get('status')
        if payload.get('status') != 'completed':
            raise ProtocolFailure('submission_failed' if payload.get('status') == 'failed' else 'submission_timeout')
        self.record['completion_ms'] = (clock() - began) * 1000

    def prepare(self, preparation_login_gate):
        self.csrf()
        if self.planned['role'] in ('startup', 'save', 'submission'):
            with preparation_login_gate:
                self.login()
        if self.planned['role'] in ('save', 'submission'):
            self.startup()
            self.manifest()
        if self.planned['role'] == 'submission':
            for action in self.planned['save_schedule']:
                self.save(action)
            self.enter(self.planned['answers'][0]['assignment_id'])
            self.record['return_verified'] = True
            self.final_checkpoint()

    def run(self, released_at):
        role = self.planned['role']
        if role == 'save':
            for index, sample in enumerate(self.record['save_samples']):
                sample['planned_monotonic_ms'] = (released_at + index * self.plan['save_interval_ms'] / 1000) * 1000
        self.record['release_skew_ms'] = (time.monotonic() - released_at) * 1000
        if role in ('login', 'full-flow'):
            self.login()
            self.record['login_ms'] = (time.monotonic() - released_at) * 1000
        if role in ('startup', 'full-flow'):
            began = time.monotonic() if role == 'full-flow' else released_at
            self.startup()
            self.record['startup_ms'] = (time.monotonic() - began) * 1000
        if role == 'full-flow':
            self.manifest()
        if role in ('full-flow', 'save'):
            for index, action in enumerate(self.planned['save_schedule']):
                if role == 'save':
                    time.sleep(max(0, self.record['save_samples'][index]['planned_monotonic_ms'] / 1000 - time.monotonic()))
                self.save(action)
            self.enter(self.planned['answers'][0]['assignment_id'])
            self.record['return_verified'] = True
            self.final_checkpoint()
        if role in ('full-flow', 'submission'):
            self.submit()
        self.record['status'] = 'completed'

    def finish_record(self):
        self.record['expected_attempts'] = list(copy.deepcopy(self.expected).values())
        self.record['http_calls'] = self.client.calls
        self.record['optional_tool_api_calls'] = sum('/research-tools/' in call.get('path', '') or '/research_tools/' in call.get('path', '') for call in self.client.calls)
        self.client.close()
        return self.record


def summarize(plan, records):
    by_id, counts = defaultdict(list), Counter(planned=plan['count'], completed=0, failed=0, missing=0, invalid=0)
    for row in records:
        by_id[row.get('account_id')].append(row)
    metrics = {key: [] for key in ('login', 'startup', 'save', 'save_scheduled', 'submission', 'completion')}
    skew = []
    for planned in plan['roster']:
        rows = by_id[planned['account_id']]
        valid = len(rows) == 1 and rows[0].get('plan_id') == plan['plan_id'] and rows[0].get('run_id') == plan['run_id']
        row = rows[0] if valid else {}
        status = row.get('status') if valid and row.get('status') in ('completed', 'failed') else ('missing' if not rows else 'invalid')
        counts[status] += 1
        observed = status in ('completed', 'failed')
        role = planned['role']
        skew.append(row.get('release_skew_ms') if observed else None)
        for metric, roles in (('login', ('login', 'full-flow')), ('startup', ('startup', 'full-flow')),
                              ('submission', ('submission', 'full-flow')), ('completion', ('submission', 'full-flow'))):
            if role in roles:
                metrics[metric].append(row.get(metric + '_ms') if observed else None)
        if role in ('save', 'full-flow'):
            samples = row.get('save_samples', [])
            for index in range(len(planned['save_schedule'])):
                metrics['save'].append(samples[index].get('duration_ms') if observed and index < len(samples) else None)
                metrics['save_scheduled'].append(samples[index].get('scheduled_ms') if observed and index < len(samples) else None)
    counts['invalid'] += sum(len(rows) for key, rows in by_id.items() if key not in {p['account_id'] for p in plan['roster']})
    report = {key: _stats(value) for key, value in metrics.items()}
    passed = counts['completed'] == plan['count'] and not counts['invalid']
    for metric in ('login', 'startup', 'save', 'save_scheduled', 'submission'):
        if report[metric]['planned']:
            for percentile in ('p95', 'p99'):
                observed = report[metric][percentile + '_ms']
                passed &= observed is not None and observed <= plan['thresholds'][f'{metric}_{percentile}_ms']
    if report['completion']['planned']:
        passed &= report['completion']['max_ms'] is not None and report['completion']['max_ms'] <= 300000
    release = _stats(skew)
    passed &= release['max_ms'] is not None and release['max_ms'] <= plan['maximum_release_skew_ms']
    return {'version': VERSION, 'plan_id': plan['plan_id'], 'run_id': plan['run_id'], 'mode': plan['mode'],
            'counts': dict(counts), **report, 'release_skew': release, 'thresholds': plan['thresholds'],
            'thresholds_met': bool(passed), 'capacity_verdict': None, 'same_DUT': True,
            'browser_operability_measured': False, 'independent_database_reconciliation_required': True,
            'optional_tool_api_calls': sum(row.get('optional_tool_api_calls', 0) for row in records),
            'login_attempt_count': sum(len(row.get('login_attempts', [])) for row in records),
            'login_busy_response_count': sum(row.get('login_busy_responses', 0) for row in records),
            'scope': 'HTTP protocol timings with same-DUT generator interference; no browser render, DOM, IndexedDB or human answerability claim.'}


def private_output(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8')


def run(plan, fixture, output):
    accounts = {row['account_id']: row for row in fixture['accounts']}
    preparation_login_gate = threading.BoundedSemaphore(plan['preparation_login_concurrency'])
    release, release_state = threading.Event(), {}
    ready = [threading.Event() for _ in plan['roster']]
    records = []
    def worker(index, planned):
        actor = Actor(accounts[planned['account_id']], planned, plan, HttpClient(plan['target_origin']))
        stage = 'preparation'
        try:
            actor.prepare(preparation_login_gate)
            ready[index].set()
            if not release.wait(180):
                raise ProtocolFailure('shared_release_timeout')
            stage = 'workload'
            actor.run(release_state['at'])
        except Exception as error:
            actor.record.update(error_code=str(error) if isinstance(error, ProtocolFailure) else type(error).__name__, failure_stage=stage)
        finally:
            ready[index].set()
        return actor.finish_record()
    with ThreadPoolExecutor(max_workers=plan['count'], thread_name_prefix='student-protocol') as pool:
        futures = [pool.submit(worker, i, row) for i, row in enumerate(plan['roster'])]
        prep_deadline = time.monotonic() + 180
        for event in ready:
            event.wait(max(0, prep_deadline - time.monotonic()))
        release_state['at'] = time.monotonic()
        release.set()
        for future in as_completed(futures):
            row = future.result()
            records.append(row)
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            output.flush()
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--mode', choices=MODES, required=True)
    parser.add_argument('--count', type=int, choices=(3, 20, 50, 180), required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--plan-output', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--summary', required=True)
    args = parser.parse_args()
    raw = Path(args.fixture).read_bytes()
    fixture = json.loads(raw.decode('utf-8-sig'))
    plan = build_plan(fixture, mode=args.mode, count=args.count, run_id=args.run_id,
                      fixture_sha256=hashlib.sha256(raw).hexdigest(), base_url=args.base_url)
    with private_output(args.plan_output) as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2, allow_nan=False)
    # Reserve every artifact before any HTTP side effect.
    with private_output(args.output) as output, private_output(args.summary) as summary:
        try:
            import resource
            initial = resource.getrusage(resource.RUSAGE_SELF)
        except ImportError:
            resource, initial = None, None
        began = time.monotonic()
        records = run(plan, fixture, output)
        report = summarize(plan, records)
        final = resource.getrusage(resource.RUSAGE_SELF) if resource else None
        report['generator_resources'] = {'wall_seconds': time.monotonic() - began,
            'user_cpu_seconds': final.ru_utime - initial.ru_utime if final else None,
            'system_cpu_seconds': final.ru_stime - initial.ru_stime if final else None,
            'peak_rss_bytes': final.ru_maxrss * 1024 if final and os.name == 'posix' else None,
            'source': 'Linux resource.getrusage(RUSAGE_SELF)' if final else 'unavailable', 'same_DUT': True}
        json.dump(report, summary, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'plan_id': plan['plan_id'], 'counts': report['counts'], 'thresholds_met': report['thresholds_met']}, allow_nan=False))
    return 0 if report['thresholds_met'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
