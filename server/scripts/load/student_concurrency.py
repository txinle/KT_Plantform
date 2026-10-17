"""Freeze and evaluate independent-host browser concurrency witnesses.

Build: --fixture FILE --mode login|login-mixed|startup-mixed|full-flow|submission --run-id ID
       --count 3|20|50|180 --attestation FILE --dut-resources FILE --output PLAN
Evaluate: --plan PLAN --records JSONL --resources FILE --output REPORT
Browser evidence alone never establishes a capacity pass. Resource and database
reconciliation remain separately reviewed evidence; capacity_verdict stays null.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

MODES = ('login', 'login-mixed', 'startup-mixed', 'full-flow', 'submission')
SOURCE_FILES = ('client/e2e/student-concurrency-witness.mjs', 'server/scripts/load/student_concurrency.py',
                'client/e2e/prepared-startup-witness.mjs', 'client/e2e/school-navigation-probe.mjs')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_roster(accounts, mode, count):
    if mode not in MODES or count not in (3, 20, 50, 180) or len(accounts) != 180:
        raise ValueError('Require an isolated 180-person fixture and explicit 3/20/50/180 denominator')
    if len({a['account_id'] for a in accounts}) != 180 or len({a['username'] for a in accounts}) != 180:
        raise ValueError('Fixture accounts must be unique')
    if mode in ('login-mixed', 'startup-mixed'):
        active = [a for a in accounts if a['initial_state'] == 'active']
        fresh = [a for a in accounts if a['initial_state'] == 'prepared']
        if len(active) != 60 or len(fresh) != 120:
            raise ValueError('Login mixed requires seed scenario mixed with 60 active and 120 fresh')
        fresh_role = 'startup' if mode == 'startup-mixed' else 'login'
        return [(a, 'save') for a in active[:count // 3]] + [(a, fresh_role) for a in fresh[:count - count // 3]]
    if mode != 'submission' and any(a['initial_state'] != 'prepared' for a in accounts):
        raise ValueError('Real login/full-flow requires a fresh same-task cohort')
    return [(a, 'submission' if mode == 'submission' else 'login') for a in accounts[:count]]


def build_plan(fixture, *, mode, count, run_id, fixture_sha256, attestation_sha256,
               resources_sha256, source_sha256, settings):
    if fixture.get('isolated_demo') is not True or fixture.get('source_ownership', {}).get('command') != 'seed_prepared_startup_load':
        raise ValueError('Use the isolated real-password seed_prepared_startup_load fixture')
    if fixture.get('run_id') != run_id or digest(fixture['fixture_spec']) != fixture['plan_sha']:
        raise ValueError('Fixture run identity/specification hash differs; seed a fresh cohort per cell')
    rows = []
    values = {'single_choice': 'A', 'multiple_choice': ['A', 'B'], 'numeric': '4', 'short_answer': 'concurrency response', 'fill_blank': '四'}
    for account, role in select_roster(fixture['accounts'], mode, count):
        questions = account['questions'][:2]
        if len(questions) != 2:
            raise ValueError('Two frozen question bindings are required')
        row = {'account_id': account['account_id'], 'role': role, 'task_id': account['taskId'],
                     'course_id': account['courseId'], 'session_id': account['session_id'],
                     'answers': [{'assignment_id': q['assignment_id'], 'value': values[q['question_type']]}
                                 for q in questions]}
        if role == 'startup':
            row.update(attempt_id=f'{run_id}:startup-mixed:{account["account_id"]}', cache_state='cold',
                       transport='browser', arrival_offset_seconds=0)
        rows.append(row)
    plan = {'version': 'student-concurrency-v1', 'harness_revision': 2, 'mode': mode, 'count': count, 'run_id': run_id,
            'fixture_sha256': fixture_sha256, 'fixture_spec_sha256': fixture['plan_sha'],
            'attestation_sha256': attestation_sha256, 'dut_resources_sha256': resources_sha256,
            'source_sha256': source_sha256, 'settings': settings, 'roster': rows,
            'save_rounds': 15, 'save_interval_ms': 2000, 'operation_timeout_ms': 60000,
            'completion_timeout_ms': 300000, 'maximum_release_skew_ms': 1000,
            'acceptance_180': count == 180, 'cache_state': 'fresh-context-cold',
            'thresholds': {'login_p95_ms': 10000, 'login_p99_ms': 20000,
                           'startup_p95_ms': 5000, 'startup_p99_ms': 10000,
                           'save_p95_ms': 1000, 'save_p99_ms': 3000,
                           'submission_p95_ms': 1000, 'submission_p99_ms': 3000, 'completion_max_ms': 300000}}
    return {**plan, 'plan_id': digest(plan)}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _stats(values):
    ordered = sorted(value if _finite(value) else math.inf for value in values)
    def quantile(q):
        value = ordered[max(0, math.ceil(len(ordered) * q) - 1)] if ordered else math.inf
        return value if math.isfinite(value) else None
    return {'planned': len(values), 'observed': sum(_finite(v) for v in values),
            'p95_ms': quantile(.95), 'p99_ms': quantile(.99), 'max_ms': quantile(1)}


def evaluate(plan, records):
    by_id = defaultdict(list)
    for row in records:
        by_id[row.get('account_id')].append(row)
    counts = Counter(planned=len(plan['roster']), completed=0, failed=0, missing=0, invalid=0, not_executed=0)
    login, saves, startup, receipts, terminal, outcomes = [], [], [], [], [], []
    for planned in plan['roster']:
        evidence = by_id[planned['account_id']]
        row = evidence[0] if len(evidence) == 1 else {}
        status = 'missing' if not evidence else 'invalid'
        if len(evidence) == 1 and row.get('plan_id') == plan['plan_id'] and row.get('run_id') == plan['run_id']:
            status = row.get('status') if row.get('status') in ('failed', 'not_executed') else 'invalid'
            valid = row.get('status') == 'completed' and row.get('fresh_context') is True
            if planned['role'] == 'login':
                valid &= row.get('ui_login') is True and _finite(row.get('login_ms')) and _finite(row.get('login_http_ms'))
            if planned['role'] == 'startup':
                raw = row.get('startup_witness', {})
                click, ready = raw.get('timestamps_ms', {}).get('click'), raw.get('timestamps_ms', {}).get('operable')
                valid &= (raw.get('status') == 'completed' and raw.get('transport') == 'browser'
                          and raw.get('attempt_id') == planned['attempt_id'] and raw.get('task_id') == planned['task_id']
                          and raw.get('cache_state') == planned['cache_state'] and bool(raw.get('phase_origin_id'))
                          and raw.get('operation_timeout_ms') == 60000
                          and raw.get('activation_receipt_ids') == [str(planned['session_id'])]
                          and _finite(click) and _finite(ready) and ready >= click
                          and ready - click <= 60000 and row.get('startup_ms') == ready - click)
            if plan['mode'] in ('full-flow', 'submission'):
                valid &= (row.get('answers_verified') == 2 and row.get('final_status') == 'completed'
                          and bool(row.get('submission_id')) and _finite(row.get('terminal_ms')))
                if 'thresholds' in plan:
                    valid &= _finite(row.get('accepted_ms')) and _finite(row.get('accepted_http_ms')) and len(row.get('save_samples', [])) == 2
                    valid &= all(_finite(sample.get('http_ms')) for sample in row.get('save_samples', []))
                    valid &= row.get('verified_assignment_ids') == [q['assignment_id'] for q in planned['answers']]
                    if plan['mode'] == 'full-flow':
                        valid &= _finite(row.get('startup_ms')) and _finite(row.get('flow_ms')) and row['flow_ms'] <= 300000
            if planned['role'] == 'save':
                valid &= len(row.get('save_samples', [])) == plan.get('save_rounds', 15)
                valid &= all(_finite(sample.get('http_ms')) and sample.get('response_version') is not None
                             for sample in row.get('save_samples', []))
            if 'maximum_release_skew_ms' in plan:
                valid &= _finite(row.get('release_skew_ms')) and row['release_skew_ms'] <= plan['maximum_release_skew_ms']
            if valid:
                status = 'completed'
        counts[status] += 1
        good = status == 'completed'
        if planned['role'] == 'login':
            login.append(row.get('login_ms') if good else None)
        if planned['role'] == 'startup':
            startup.append(row.get('startup_ms') if good else None)
        if planned['role'] == 'save':
            samples = row.get('save_samples', [])
            saves.extend(samples[n].get('http_ms') if good and n < len(samples) else None
                         for n in range(plan.get('save_rounds', 15)))
        if plan['mode'] in ('full-flow', 'submission'):
            terminal.append(row.get('terminal_ms') if good else None)
            receipts.append(row.get('accepted_ms') if good else None)
            if plan['mode'] == 'full-flow':
                startup.append(row.get('startup_ms') if good else None)
                samples = row.get('save_samples', [])
                saves.extend(samples[n].get('http_ms') if good and n < len(samples) else None for n in range(2))
        outcomes.append({'account_id': planned['account_id'], 'role': planned['role'], 'outcome': status,
                         'error_code': row.get('error_code')})
    stats = {name: _stats(values) for name, values in
             [('login', login), ('save', saves), ('startup', startup), ('submission_receive', receipts), ('terminal', terminal)]}
    thresholds_met = counts['completed'] == counts['planned'] and counts['planned'] > 0
    for key, p95, p99 in [('login', 10000, 20000), ('save', 1000, 3000), ('startup', 5000, 10000), ('submission_receive', 1000, 3000)]:
        if stats[key]['planned']:
            thresholds_met &= stats[key]['p95_ms'] is not None and stats[key]['p95_ms'] <= p95
            thresholds_met &= stats[key]['p99_ms'] is not None and stats[key]['p99_ms'] <= p99
    if terminal:
        thresholds_met &= stats['terminal']['max_ms'] is not None and stats['terminal']['max_ms'] <= 300000
    unknown = sum(len(rows) for key, rows in by_id.items() if key not in {p['account_id'] for p in plan['roster']})
    thresholds_met &= unknown == 0
    return {'version': 'student-concurrency-evaluation-v1', 'plan_id': plan['plan_id'], 'run_id': plan['run_id'],
            'mode': plan['mode'], 'counts': dict(counts), **stats, 'outcomes': outcomes, 'unknown_records': unknown,
            'thresholds_met': bool(thresholds_met), 'acceptance_180': plan['count'] == 180,
            'capacity_verdict': None,
            'scope': 'Single frozen cell. Full denominators; missing/failed latency is infinite (serialized null). '
                     'Review independent-host resources, DB answer reconciliation, and two fresh repeats separately.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('fixture', 'mode', 'run-id', 'attestation', 'dut-resources', 'plan', 'records', 'resources'):
        parser.add_argument('--' + name, **({'choices': MODES} if name == 'mode' else {}))
    parser.add_argument('--count', type=int, choices=(3, 20, 50, 180), default=180)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if args.plan:
        plan = read(args.plan)
        if digest({k: v for k, v in plan.items() if k != 'plan_id'}) != plan['plan_id']:
            parser.error('Frozen plan hash differs')
        if not args.records or not args.resources:
            parser.error('Evaluation requires --records and --resources')
        resources = read(args.resources)
        if resources.get('plan_id') != plan['plan_id'] or resources.get('run_id') != plan['run_id']:
            parser.error('Resource report binding differs')
        records = [json.loads(line) for line in Path(args.records).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        result = evaluate(plan, records)
        result['resource_report_sha256'] = file_hash(args.resources)
        result['resource_run_aborted'] = resources.get('aborted') is True
        if result['resource_run_aborted']:
            result['thresholds_met'] = False
    else:
        if not all((args.fixture, args.mode, args.run_id, args.attestation, args.dut_resources)):
            parser.error('Build requires fixture, mode, run-id, attestation, dut-resources')
        root = Path(__file__).resolve().parents[3]
        resources = read(args.dut_resources)
        result = build_plan(read(args.fixture), mode=args.mode, count=args.count, run_id=args.run_id,
            fixture_sha256=file_hash(args.fixture), attestation_sha256=file_hash(args.attestation),
            resources_sha256=file_hash(args.dut_resources), source_sha256={p: file_hash(root / p) for p in SOURCE_FILES},
            settings=resources['settings'])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == '__main__':
    main()
