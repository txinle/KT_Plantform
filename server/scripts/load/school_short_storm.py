#!/usr/bin/env python3
"""Short synchronized capture storms on separate fixtures; no full-capacity claim."""
import argparse
import concurrent.futures
import json
import hashlib
import os
import re
import statistics
import threading
import time
import uuid
from pathlib import Path

from assessment_300 import Runner, StudentState, percentile
from capture_protocol import CaptureProtocolMixin, CONTRACT, evidence_files


class ShortRunner(Runner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ledger = {}
        self.released_at = None
        self.send_delays = []

    def request(self, state, path, *, method='GET', body=None):
        began = time.perf_counter()
        if body and state.session_id:
            with self.lock:
                row = self.ledger.setdefault(str(state.session_id), {'event_ids': [], 'events': {}, 'submission_tokens': []})
                for event in body.get('events', []):
                    token = event['event_id']
                    expected = {'payload_sha256': payload_hash(event['payload']), 'client_instance_id': body['client_instance_id'],
                        'client_sequence_no': event['client_sequence_no'], 'acknowledged': False}
                    if token not in row['events']:
                        row['events'][token] = expected
                        row['event_ids'].append(token)
                if 'submission_token' in body and body['submission_token'] not in row['submission_tokens']:
                    row['submission_tokens'].append(body['submission_token'])
        operation_id = uuid.uuid4().hex
        for retry in range(2):
            self.request_context.current = {'operation_id': operation_id, 'attempt': retry + 1}
            try:
                status, payload, _ = super().request(state, path, method=method, body=body)
                break
            except RuntimeError as error:
                if retry == 1 or re.fullmatch(r'HTTP 4\d{2}', str(error)):
                    raise
        if body and body.get('events'):
            expected_ack = {(event['event_id'], event['client_sequence_no']) for event in body['events']}
            actual_ack = {(ack.get('event_id'), ack.get('client_sequence_no')) for ack in payload.get('acknowledgements', [])}
            if status != 200 or payload.get('success') is not True or payload.get('rejections') or actual_ack != expected_ack or len(payload.get('acknowledgements', [])) != len(expected_ack):
                raise RuntimeError('Event acknowledgement mismatch')
            with self.lock:
                for token, _ in expected_ack:
                    self.ledger[str(state.session_id)]['events'][token]['acknowledged'] = True
        return status, payload, time.perf_counter() - began

    def start(self, state):
        began = time.perf_counter()
        with self.lock:
            self.send_delays.append(began - self.released_at)
        status, payload, _ = self.request(state, f'/api/v1/student/tasks/{self.task_id}/start/', method='POST', body={})
        self.record('start', time.perf_counter() - self.released_at)
        if status not in (200, 201):
            raise RuntimeError('Start failed')
        state.session_id = int(payload['session_id'])
        state.assignment_ids = [int(payload['question_url'].rstrip('/').split('/')[-1])]

    def resume(self, state):
        status, payload, seconds = self.request(state, f'/api/v1/student/tasks/{self.task_id}/resume/', method='POST', body={})
        self.record('resume', seconds)
        if status != 200 or int(payload['session_id']) != state.session_id:
            raise RuntimeError('Resume did not return the existing session')

    def phase(self, name, states, operation, workers, *, synchronized=True):
        if not states:
            return
        barrier = threading.Barrier(len(states), action=lambda: setattr(self, 'released_at', time.perf_counter())) if synchronized else None

        def invoke(state):
            try:
                if barrier is not None:
                    barrier.wait(timeout=30)
                operation(state)
            except Exception as error:
                state.workflow_failed = True
                with self.lock:
                    row = {'phase': name, 'error': type(error).__name__}
                    match = re.fullmatch(r'HTTP (\d{3})', str(error))
                    if match: row['http_status'] = int(match.group(1))
                    if str(error) in ('Event acknowledgement mismatch','Start failed','Resume did not return the existing session'):
                        row['reason'] = str(error)
                    self.errors.append(row)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(invoke, states))


class ProtocolShortRunner(CaptureProtocolMixin, ShortRunner):
    pass


def run_phases(runner, states):
    runner.phase('login', states, runner.login, 6, synchronized=False)
    for name in ('start', 'resume', 'answer'):
        states = [state for state in states if not getattr(state, 'workflow_failed', False)]
        if not states:
            break
        runner.phase(name, states, getattr(runner, name), len(states), synchronized=True)


def item_interval(count):
    return 0 if count == 3 else 18


def payload_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def checks(result):
    n = result['student_count']
    metrics = result['metrics']
    def bound(key, percentile_key, limit):
        value = metrics.get(key, {}).get(percentile_key)
        return value is not None and value <= limit
    counts = {key: n * (20 if key in ('enter_item', 'save_draft', 'event_batch', 'submit_item') else 1)
              for key in ('login', 'start', 'resume', 'enter_item', 'save_draft', 'event_batch', 'submit_item', 'finish')}
    protocol = result.get('capture_protocol')
    complete = all((metrics.get(key, {}).get('count', 0) >= count if protocol and key in ('save_draft', 'event_batch')
                    else metrics.get(key, {}).get('count') == count) for key, count in counts.items())
    protocol_complete = not protocol or (protocol.get('contract') == CONTRACT and protocol.get('all_events_acknowledged') is True
        and protocol.get('completed_sessions') == n and all(protocol.get('scenarios', {}).get(kind, 0) >= n
            for kind in ('single_choice', 'true_false', 'multiple_choice', 'fill_blank', 'short_answer',
                         'ime_protocol_simulation', 'queued_replay_reload_protocol', 'writer_lease_reacquire',
                         'required_check', 'hint_observed_protocol', 'authorized_feedback_protocol')))
    return {'complete_workflow': complete, 'capture_protocol_complete': protocol_complete,
        'zero_workflow_errors': not result['errors'], 'zero_http_errors': result['http_error_count'] == 0,
        'all_unique_sessions': result['session_count'] == n and result['unique_session_count'] == n,
        'start_p95': bound('start', 'p95_seconds', 5), 'start_p99': bound('start', 'p99_seconds', 10),
        'save_p95': bound('save_draft', 'p95_seconds', 1), 'events_p95': bound('event_batch', 'p95_seconds', 1),
        'submit_p95': bound('submit_item', 'p95_seconds', 2), 'finish_p95': bound('finish', 'p95_seconds', 2),
        'enter_p95': bound('enter_item', 'p95_seconds', 1), 'enter_p99': bound('enter_item', 'p99_seconds', 2),
        'save_p99': bound('save_draft', 'p99_seconds', 2), 'events_p99': bound('event_batch', 'p99_seconds', 2),
        'submit_p99': bound('submit_item', 'p99_seconds', 5), 'finish_p99': bound('finish', 'p99_seconds', 5)}


def summary(values):
    return {'count': len(values), 'p50_seconds': percentile(values, .5), 'p95_seconds': percentile(values, .95),
            'p99_seconds': percentile(values, .99), 'max_seconds': round(max(values), 3) if values else None}


def main():
    import resource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--timeout', type=int, default=35)
    args = parser.parse_args()
    if not 0 < args.timeout <= 35:
        raise SystemExit('Request timeout must be at most 35 seconds')
    path = Path(args.manifest)
    manifest = json.loads(path.read_text())
    n = manifest['student_count']
    if manifest.get('database') != 'kt_e2e' or n not in (3, 50, 150, 300) or len(set(manifest['usernames'])) != n or manifest['item_count'] != 20 or not manifest.get('source_config'):
        raise SystemExit('Dedicated short-load manifest required')
    states = [StudentState(name, manifest['password']) for name in manifest['usernames']]
    if manifest.get('capture_protocol') not in (None, CONTRACT):
        raise SystemExit('Unknown capture protocol contract')
    runner_type = ProtocolShortRunner if manifest.get('capture_protocol') == CONTRACT else ShortRunner
    runner = runner_type(args.base_url, manifest['task_id'], args.timeout, item_interval=item_interval(n))
    runner.protocol_course_id, runner.protocol_experiment_id = manifest['course_id'], manifest['experiment_id']
    attempts_path = Path(args.output).with_suffix('.attempts.jsonl')
    before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.time()
    with evidence_files(path, args.output) as files:
        files['runner'] = runner
        runner.attempt_output = files['attempts']
        run_phases(runner, states)
        runner.attempt_output = None
        ended = time.time()
        after = resource.getrusage(resource.RUSAGE_SELF)
        result = {'mode': 'isolated_short_storm', 'formal_capacity_accepted': False, 'student_count': n,
        'source_config': manifest['source_config'], 'started': started, 'ended': ended,
        'item_interval_seconds': item_interval(n), 'answer_duration_target_seconds': 20 * item_interval(n),
        'metrics': {key: summary(values) for key, values in runner.metrics.items()},
        'errors': runner.errors, 'http_request_count': runner.request_count, 'http_error_count': runner.request_errors,
        'attempt_summary': runner.attempt_summary(), 'request_attempts_file': attempts_path.name,
        'item_arrivals': runner.item_arrivals,
        'session_count': sum(state.session_id is not None for state in states),
        'unique_session_count': len({state.session_id for state in states if state.session_id}),
        'generator': {'cpu_seconds': after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
            'max_rss_bytes': after.ru_maxrss * 1024, 'send_delay': summary(runner.send_delays),
            'cpu_max': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
            'memory_max': Path('/sys/fs/cgroup/memory.max').read_text().strip()}}
        if isinstance(runner, ProtocolShortRunner):
            result['capture_protocol'] = runner.protocol_summary()
        result['checks'] = checks(result)
        result['passed'] = all(result['checks'].values())
        json.dump(result, files['result'], indent=2)
        files['complete'] = not runner.errors
    print(json.dumps({'student_count': n, 'passed': result['passed'], 'checks': result['checks']}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
