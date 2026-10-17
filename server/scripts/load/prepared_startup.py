"""Evaluate frozen startup witnesses, preserving every planned failure denominator.

This evaluates evidence files. It does not execute load or establish capacity.
All phase timestamps in one witness must share its stated monotonic origin.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path

PHASES = ('click', 'admission_requested', 'admission_granted', 'activation_started',
          'activation_completed', 'first_question_requested', 'first_question_received',
          'runtime_initialized', 'operable')
INTERVALS = {'queue': ('admission_requested', 'admission_granted'),
             'activation': ('activation_started', 'activation_completed'),
             'first_transfer': ('first_question_requested', 'first_question_received'),
             'initialization': ('first_question_received', 'runtime_initialized'),
             'click_to_operable': ('click', 'operable')}


def _hash(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def run_binding(scenario, run_id):
    """A frozen repeat can bind a separate, never-activated student cohort."""
    return scenario['run_bindings'][run_id] if 'run_bindings' in scenario else scenario


def build_plan(scenarios, *, settings, run_ids):
    pilot = settings.get('stage') in ('resource_pilot', 'configuration_pilot')
    if len(scenarios) < (1 if pilot else 5) or len(run_ids) < (1 if pilot else 2) or len(set(run_ids)) != len(run_ids):
        raise ValueError('Require at least five scenarios and two distinct frozen runs')
    if len({row['scenario_id'] for row in scenarios}) != len(scenarios):
        raise ValueError('Scenario IDs must be unique')
    for scenario in scenarios:
        if 'run_bindings' in scenario and set(scenario['run_bindings']) != set(run_ids):
            raise ValueError('Every repeat requires its explicit frozen roster binding')
        for run_id in run_ids:
            binding = run_binding(scenario, run_id)
            for field, id_field in [('planned_startups', 'attempt_id'), ('planned_saves', 'operation_id')]:
                rows = binding[field]
                if len({row[id_field] for row in rows}) != len(rows):
                    raise ValueError(f'Duplicate planned {id_field}')
            for row in binding['planned_startups']:
                if row['transport'] not in ('browser', 'protocol') or row['cache_state'] not in ('cold', 'warm'):
                    raise ValueError('Every startup declares browser/protocol and cold/warm')
    plan = copy.deepcopy({'version': 'prepared-startup-v1', 'settings': settings,
                          'run_ids': run_ids, 'scenarios': scenarios})
    return {**plan, 'input_settings_hash': _hash(plan)}


def build_fixture_plan(cells, *, settings, run_ids):
    """Bind fresh seeded cohorts to executable browser actions, never credentials."""
    scenarios, accounts, used = {}, [], set()
    for cell in cells:
        fixture, scenario_id, run_id = cell['fixture'], cell['scenario_id'], cell['run_id']
        if scenario_id not in ('cold-burst', 'warm-burst', 'three-task', 'mixed', 'reload-recovery'):
            raise ValueError('Unsupported executable scenario')
        if fixture.get('isolated_demo') is not True or len(fixture['accounts']) != 180:
            raise ValueError('Each cell requires an isolated fresh 180-person fixture')
        frozen_settings = fixture['settings_binding']
        if (frozen_settings['ASSESSMENT_ADMISSION_CAPACITY'] != settings['C']
                or frozen_settings['ASSESSMENT_ADMISSION_RATE_PER_SECOND'] != settings['R']):
            raise ValueError('Fixture C/R differs from actual frozen candidate settings')
        scenario = scenarios.setdefault(scenario_id, {'scenario_id': scenario_id, 'run_bindings': {}})
        if run_id not in run_ids or run_id in scenario['run_bindings']:
            raise ValueError('Duplicate or unknown scenario/run cell')
        startups, saves = [], []
        for index, account in enumerate(fixture['accounts']):
            if account['account_id'] in used:
                raise ValueError('Fresh first-activation cohorts cannot be reused across cells')
            used.add(account['account_id'])
            accounts.append(account)
            stem = f'{run_id}:{scenario_id}:{account["account_id"]}'
            offset = index * settings.get('arrival_seconds', 0) / 179
            row = {'account_id': account['account_id'], 'task_id': account['taskId'],
                   'arrival_offset_seconds': offset}
            if account['initial_state'] == 'active':
                if scenario_id != 'mixed':
                    raise ValueError('Startup roster contains an already activated account')
                first, second = account['questions'][:2]
                actions = [{'operation_id': f'{stem}:save1', 'kind': 'save', 'assignment_id': first['assignment_id'],
                            'value': account['save_value']},
                           {'operation_id': f'{stem}:switch', 'kind': 'switch', 'assignment_id': second['assignment_id']},
                           {'operation_id': f'{stem}:save2', 'kind': 'save', 'assignment_id': second['assignment_id'],
                            'value': _fixture_value(second['question_type'])},
                           {'operation_id': f'{stem}:submit', 'kind': 'submit'}]
                saves.append({**row, 'operation_id': f'{stem}:mixed', 'question_url': account['question_url'],
                              'save_value': account['save_value'], 'actions': actions})
            else:
                cache = cell.get('cache_state', 'warm' if scenario_id == 'warm-burst' else 'cold')
                startup = {**row, 'attempt_id': stem, 'transport': 'browser', 'cache_state': cache}
                if cache == 'warm':
                    startup['warm_urls'] = account['warm_urls']
                if scenario_id == 'reload-recovery':
                    first = account['questions'][0]
                    value = _fixture_value(first['question_type'])
                    startup['post_start_actions'] = [
                        {'operation_id': f'{stem}:save', 'kind': 'save', 'assignment_id': first['assignment_id'], 'value': value},
                        {'operation_id': f'{stem}:reload', 'kind': 'reload', 'assignment_id': first['assignment_id'], 'value': value}]
                startups.append(startup)
        if scenario_id == 'mixed' and (len(saves), len(startups)) != (60, 120):
            raise ValueError('Mixed cell requires exactly 60 active and 120 never-activated accounts')
        if scenario_id == 'three-task' and sorted(sum(row['task_id'] == task for row in startups)
                for task in {row['task_id'] for row in startups}) != [60, 60, 60]:
            raise ValueError('Three-task cell requires three distinct tasks of 60 accounts')
        scenario['run_bindings'][run_id] = {'planned_startups': startups, 'planned_saves': saves,
            'fixture_sha256': cell.get('fixture_sha256') or _hash(fixture), 'fixture_spec_sha256': fixture.get('plan_sha')}
    plan = build_plan(list(scenarios.values()), settings={**settings, 'browser_actions_version': 1,
        'browser_cache_scope': 'Fresh account contexts; warm primes only explicit prepared manifest/question URLs after login.'}, run_ids=run_ids)
    return plan, {'isolated_demo': True, 'accounts': accounts, 'input_settings_hash': plan['input_settings_hash']}


def _fixture_value(kind):
    return {'single_choice': 'A', 'multiple_choice': ['A', 'B'], 'numeric': '4',
            'short_answer': '隔离浏览器操作见证。', 'fill_blank': '四', 'true_false': True}[kind]


def _stats(values):
    values = sorted(values)
    if not values:
        return {'sample_count': 0, 'p50': None, 'p95': None, 'p99': None, 'max': None}
    def percentile(fraction):
        position = (len(values) - 1) * fraction
        low, high = math.floor(position), math.ceil(position)
        return values[low] + (values[high] - values[low]) * (position - low)
    return {'sample_count': len(values), 'p50': percentile(.5), 'p95': percentile(.95),
            'p99': percentile(.99), 'max': max(values)}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _startup_outcome(planned, records, settings_hash, direct=False):
    if not records:
        return {'outcome': 'missing', 'code': 'witness_missing', 'duplicate_activations': 0}
    if len(records) != 1:
        return {'outcome': 'failed', 'code': 'duplicate_witness', 'duplicate_activations': 0}
    row = records[0]
    if row.get('input_settings_hash') != settings_hash:
        return {'outcome': 'incomplete', 'code': 'frozen_material_binding_mismatch', 'duplicate_activations': 0}
    if row.get('status') == 'not_executed':
        return {'outcome': 'not_executed', 'code': row.get('error_code') or 'not_executed', 'duplicate_activations': 0}
    receipts = row.get('activation_receipt_ids', [])
    duplicates = max(0, len(set(receipts)) - 1) if isinstance(receipts, list) else 0
    if duplicates:
        return {'outcome': 'failed', 'code': 'duplicate_effective_activation', 'duplicate_activations': duplicates}
    if row.get('status') != 'completed':
        return {'outcome': 'failed', 'code': row.get('error_code') or 'startup_failed', 'duplicate_activations': 0}
    timestamps = row.get('timestamps_ms', {})
    phases = tuple(phase for phase in PHASES if not direct or phase not in ('admission_granted', 'activation_started'))
    values = [timestamps.get(phase) for phase in phases]
    if (row.get('transport') != planned['transport'] or row.get('task_id') != planned['task_id']
            or not row.get('phase_origin_id') or not isinstance(receipts, list) or len(set(receipts)) != 1
            or not all(_number(value) for value in values) or values != sorted(values)):
        return {'outcome': 'incomplete', 'code': 'phase_evidence_incomplete', 'duplicate_activations': 0}
    intervals = {**INTERVALS}
    if direct:
        intervals.pop('queue')
        intervals['activation'] = ('admission_requested', 'activation_completed')
    return {'outcome': 'completed', 'code': '', 'duplicate_activations': 0,
            'durations': {name: timestamps[end] - timestamps[start]
                          for name, (start, end) in intervals.items()}}


def _counts(rows):
    return {'planned': len(rows), **{key: sum(row['outcome'] == key for row in rows)
                                    for key in ('completed', 'failed', 'missing', 'incomplete', 'not_executed')}}


def _startup_summary(rows):
    summary = _counts(rows)
    summary['durations_ms'] = {name: _stats([row['durations'][name] for row in rows
                                            if row['outcome'] == 'completed' and name in row['durations']
                                            and (name != 'click_to_operable' or row['transport'] == 'browser')])
                               for name in INTERVALS}
    summary['durations_ms']['click_to_protocol_ready'] = _stats([
        row['durations']['click_to_operable'] for row in rows
        if row['outcome'] == 'completed' and row['transport'] == 'protocol'])
    return summary


def _action_outcomes(planned, parent_records, settings_hash, binding):
    records = defaultdict(list)
    if len(parent_records) == 1:
        for row in parent_records[0].get('action_results', []):
            records[row.get('operation_id')].append(row)
    result = []
    for action in planned:
        evidence = records[action['operation_id']]
        outcome, code = 'missing', 'action_witness_missing'
        values = {}
        if len(evidence) > 1:
            outcome, code = 'failed', 'duplicate_action_witness'
        elif evidence:
            row = evidence[0]
            if len(parent_records) != 1 or parent_records[0].get('input_settings_hash') != settings_hash:
                outcome, code = 'incomplete', 'frozen_material_binding_mismatch'
            elif row.get('kind') != action['kind']:
                outcome, code = 'incomplete', 'action_kind_mismatch'
            else:
                outcome = row.get('status') if row.get('status') in ('completed', 'failed', 'not_executed') else 'incomplete'
                code = row.get('error_code', '')
                if outcome == 'completed' and action['kind'] == 'reload' and not (
                        row.get('recovery_type') == 'reload_after_saved_draft'
                        and row.get('ui_value_verified') is True and row.get('server_pointer_verified') is True
                        and row.get('target_event_id') and type(row.get('response_version')) is int):
                    outcome, code = 'incomplete', 'reload_evidence_incomplete'
                if outcome == 'completed' and action['kind'] == 'submit' and not (
                        row.get('accepted_http_status') == 202 and row.get('submission_id')
                        and row.get('final_status') == 'completed' and row.get('completed_page_path')):
                    outcome, code = 'incomplete', 'submission_completion_evidence_incomplete'
                values = {key: row[key] for key in ('editing_experience_ms', 'draft_http_ms')
                          if _number(row.get(key)) and row[key] >= 0}
        elif len(parent_records) == 1 and parent_records[0].get('status') == 'not_executed':
            outcome, code = 'not_executed', parent_records[0].get('error_code', 'not_executed')
        result.append({**binding, **action, **values, 'outcome': outcome, 'code': code})
    return result


def _action_summary(rows):
    return {**_counts(rows), 'editing_experience_ms': _stats([row['editing_experience_ms'] for row in rows
            if row['outcome'] == 'completed' and 'editing_experience_ms' in row]),
            'draft_http_ms': _stats([row['draft_http_ms'] for row in rows
            if row['outcome'] == 'completed' and 'draft_http_ms' in row])}


def evaluate(plan, startup_records, save_records):
    material = {key: value for key, value in plan.items() if key != 'input_settings_hash'}
    if _hash(material) != plan['input_settings_hash']:
        raise ValueError('Frozen startup settings or planned IDs changed')
    startups_by_key, saves_by_key = defaultdict(list), defaultdict(list)
    for records, grouped, identity in [(startup_records, startups_by_key, 'attempt_id'),
                                       (save_records, saves_by_key, 'operation_id')]:
        for row in records:
            grouped[(row.get('run_id'), row.get('scenario_id'), row.get(identity))].append(row)
    rows, saves, mixed_actions, recovery_actions = [], [], [], []
    per_task, per_run_scenario = defaultdict(list), defaultdict(list)
    planned_keys, planned_save_keys = set(), set()
    for run_id in plan['run_ids']:
        for scenario in plan['scenarios']:
            scenario_id = scenario['scenario_id']
            binding = run_binding(scenario, run_id)
            for planned in binding['planned_startups']:
                key = (run_id, scenario_id, planned['attempt_id'])
                planned_keys.add(key)
                outcome = _startup_outcome(planned, startups_by_key[key], plan['input_settings_hash'],
                                           direct=plan['settings'].get('startup_protocol') == 'direct')
                row = {**planned, 'run_id': run_id, 'scenario_id': scenario_id, **outcome}
                rows.append(row)
                per_task[str(planned['task_id'])].append(row)
                per_run_scenario[f'{run_id}:{scenario_id}'].append(row)
                recovery_actions.extend(_action_outcomes(planned.get('post_start_actions', []),
                    startups_by_key[key], plan['input_settings_hash'], {'run_id': run_id, 'scenario_id': scenario_id}))
            for planned in binding['planned_saves']:
                key = (run_id, scenario_id, planned['operation_id'])
                planned_save_keys.add(key)
                evidence = saves_by_key[key]
                outcome, code, duration = 'missing', 'save_witness_missing', None
                if len(evidence) > 1:
                    outcome, code = 'failed', 'duplicate_save_witness'
                elif evidence:
                    witness = evidence[0]
                    outcome, code = 'failed', witness.get('error_code') or 'save_failed'
                    if witness.get('status') == 'not_executed':
                        outcome = 'not_executed'
                    if witness.get('status') == 'completed':
                        begin, end = witness.get('started_at_ms'), witness.get('completed_at_ms')
                        outcome, code = 'incomplete', 'save_time_evidence_incomplete'
                        if (witness.get('input_settings_hash') == plan['input_settings_hash']
                                and _number(begin) and _number(end) and end >= begin
                                and witness.get('task_id') == planned['task_id']):
                            outcome, code, duration = 'completed', '', end - begin
                saves.append({**planned, 'run_id': run_id, 'scenario_id': scenario_id,
                              'outcome': outcome, 'code': code, 'duration_ms': duration})
                mixed_actions.extend(_action_outcomes(planned.get('actions', []), evidence,
                    plan['input_settings_hash'], {'run_id': run_id, 'scenario_id': scenario_id}))
    browser = _startup_summary([row for row in rows if row['transport'] == 'browser'])
    protocol = _startup_summary([row for row in rows if row['transport'] == 'protocol'])
    protocol['durations_ms'].pop('click_to_operable')
    return {'version': 'prepared-startup-evaluation-v1', 'input_settings_hash': plan['input_settings_hash'],
            'stage': plan['settings'].get('stage', 'full_protocol'),
            'capacity_verdict': None,
            'evidence_scope': 'Frozen witness accounting; protocol timing is not browser operability.',
            'startups': _counts(rows), 'browser_operability': browser, 'protocol_startups': protocol,
            'mixed_saves': {**_counts(saves), 'durations_ms': _stats([row['duration_ms'] for row in saves
                                                                   if row['outcome'] == 'completed'])},
            'mixed_actions': _action_summary(mixed_actions),
            'recovery_actions': _action_summary(recovery_actions),
            'recovery_scope': 'Actual browser reload after saved draft; excludes lost activation responses, queue interruption and server/worker restarts.',
            'save_timing_scope': 'editing_experience includes UI/debounce; draft_http measures requestStart to responseEnd, including network and HTTP service.',
            'mixed_action_outcomes': mixed_actions, 'recovery_action_outcomes': recovery_actions,
            'duplicate_startup_records': sum(max(0, len(startups_by_key[key]) - 1) for key in planned_keys),
            'duplicate_save_records': sum(max(0, len(saves_by_key[key]) - 1) for key in planned_save_keys),
            'unknown_startup_records': sum(len(evidence) for key, evidence in startups_by_key.items() if key not in planned_keys),
            'unknown_save_records': sum(len(evidence) for key, evidence in saves_by_key.items() if key not in planned_save_keys),
            'duplicate_effective_activations': sum(row['duplicate_activations'] for row in rows),
            'fairness_per_task': {key: _startup_summary(value) for key, value in sorted(per_task.items())},
            'per_run_scenario': {key: _startup_summary(value) for key, value in sorted(per_run_scenario.items())},
            'per_cache_state': {state: _startup_summary([row for row in rows if row['cache_state'] == state])
                                for state in ('cold', 'warm')},
            'startup_outcomes': rows, 'save_outcomes': saves}


def _read_records(path):
    content = Path(path).read_text(encoding='utf-8-sig')
    if content.lstrip().startswith('['):
        return json.loads(content)
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', help='Existing frozen plan to evaluate')
    parser.add_argument('--startups')
    parser.add_argument('--saves')
    parser.add_argument('--cells', help='Build a plan: JSON list of scenario_id/run_id/fixture(path) cells')
    parser.add_argument('--settings', help='Build a plan: JSON settings with actual C/R and stage/source bindings')
    parser.add_argument('--run-ids', help='Build a plan: comma-separated fixed repeat IDs')
    parser.add_argument('--accounts-output', help='Build a plan: fresh private merged accounts file (0600)')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.cells:
        if not all((args.settings, args.run_ids, args.accounts_output)):
            parser.error('--cells requires --settings, --run-ids and --accounts-output')
        cells_path = Path(args.cells).resolve()
        cells = json.loads(cells_path.read_text(encoding='utf-8-sig'))
        for cell in cells:
            path = (cells_path.parent / cell['fixture']).resolve()
            data = path.read_bytes()
            cell['fixture'] = json.loads(data.decode('utf-8-sig'))
            cell['fixture_sha256'] = hashlib.sha256(data).hexdigest()
        settings = json.loads(Path(args.settings).read_text(encoding='utf-8-sig'))
        root = Path(__file__).resolve().parents[3]
        settings['harness_source_sha256'] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ('server/scripts/load/prepared_startup.py', 'client/e2e/prepared-startup-witness.mjs')}
        plan, accounts = build_fixture_plan(cells,
            settings=settings,
            run_ids=args.run_ids.split(','))
        output, private = Path(args.output), Path(args.accounts_output)
        if output.exists() or private.exists():
            parser.error('Build outputs already exist; refusing fixture/plan overwrite')
        output.parent.mkdir(parents=True, exist_ok=True)
        private.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
            json.dump(accounts, stream, ensure_ascii=False)
        output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'plan_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                          'input_settings_hash': plan['input_settings_hash'],
                          'scenario_count': len(plan['scenarios']), 'run_ids': plan['run_ids'],
                          'private_accounts': len(accounts['accounts'])}))
        return
    if not all((args.plan, args.startups, args.saves)):
        parser.error('Evaluation requires --plan, --startups and --saves')
    result = evaluate(json.loads(Path(args.plan).read_text(encoding='utf-8-sig')),
                      _read_records(args.startups), _read_records(args.saves))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
