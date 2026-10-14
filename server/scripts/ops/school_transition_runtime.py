#!/usr/bin/env python3
"""Bound isolated staged acceptance; deployment and browser launch remain external.

Required additions to DiagnosticRuntime's config: history_root, browser_bridge_dir,
driver_dir (server directory), driver_files (absolute file -> SHA256),
browser_tool_files (logical tool -> SHA256), teacher_probe_path/teacher_probe_sha256.
source_config additionally freezes phase, baseline_commit, diagnostics_mode,
session_timeout_seconds and source files. --seed-only creates unbound fixtures;
after all task IDs are allowlisted --prepare-only freezes source/fixture/plan.
Deploy the corresponding immutable variant externally before --start-at.
"""
import argparse
import configparser
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'load'))
from school_architecture_runtime import ArchitectureRuntime, confirmed_fact_failure
from school_diagnostic_runtime import (BASE_URL, DATABASE, PROJECT, SIX_POOL_PROFILE, SIX_WORKERS,
    SIX_MEMORY, SIX_CPU_SHARES, six_workers_for_source, validate_owned_postgres_settings, SafetyInterrupted, fingerprint, read, read_evidence, write_once)
from school_worker_short import armed_matches, short_stop_reason, protocol_finished_error
from natural_workload import CONFIDENCE_ONLY, build_workload, validate_workload
from school_natural_load import observed_active_overlap, LEASE_RECOVERY_PROFILE, lease_recovery_accounting
from school_short_storm import summary
from capture_protocol import digest

STAGES = ('baseline10', 'candidate10', 'candidate50', 'login180', 'natural180-r1', 'natural180-r2')
DIAGNOSTIC_STAGES = ('diagnostic180', 'diagnostic2')
COUNTS = dict(zip(STAGES, (10, 10, 50, 180, 180, 180)))
COUNTS['diagnostic180'] = 180
COUNTS['diagnostic2'] = 2
SEEDS = {stage: 20261014 if stage == 'natural180-r2' else 20261013 for stage in STAGES + DIAGNOSTIC_STAGES}
THRESHOLDS = {'login_entry_to_authentication_end': (5, 10), 'login_to_home_ready': (5, 10),
    'start_to_first_question_ready': (5, 10), 'navigation_next_ready': (1, 2),
    'navigation_return_ready': (1, 2), 'submit_next_ready': (1.5, 3),
    'save_draft': (1, 3), 'finish_ready': (3, 5)}
BROWSER_THRESHOLDS = {'login': (5, 10), 'task_list': (5, 10), 'start_first_ready': (5, 10),
    'navigation_next_ready': (1, 2), 'navigation_return_ready': (1, 2),
    'submit_next_ready': (1.5, 3), 'save_draft': (1, 3), 'finish_ready': (3, 5)}
SIX_STAGES = tuple(stage for stage in STAGES if stage != 'login180')
HOST_BUDGET = {'vcpu': 16, 'memory_bytes': 16 * 1024**3}
DURABILITY = {'fsync': 'on', 'synchronous_commit': 'on', 'full_page_writes': 'on'}


def validate_transition_source(source):
    if source.get('load_evidence_profile') not in (None, LEASE_RECOVERY_PROFILE):
        raise SafetyInterrupted('Unknown frozen load evidence profile')
    if (source.get('phase') not in ('baseline', 'candidate')
            or not source.get('baseline_commit', '').startswith('e64535f')
            or source.get('session_timeout_seconds') != 5 or source.get('diagnostics_mode') != 'light'
            or source.get('admission', {}).get('capacity') != 8):
        raise SafetyInterrupted('Only literal baseline and fixed-resource light-diagnostic comparison permitted')
    profile = source.get('pool_profile')
    if profile == SIX_POOL_PROFILE:
        if (source['phase'] != 'candidate' or source.get('host_budget') != HOST_BUDGET
                or source.get('durability') != DURABILITY or source.get('workload_profile') != 'natural-180-v1'):
            raise SafetyInterrupted('Six-pool candidate requires fixed host, durability and workload bindings')
        for role, workers in six_workers_for_source(source).items():
            expected = source.get('roles', {}).get(role, {})
            if (expected.get('workers') != workers or expected.get('memory_bytes') != SIX_MEMORY[role]
                    or expected.get('cpus') != 0 or expected.get('cpu_shares') != SIX_CPU_SHARES[role]):
                raise SafetyInterrupted('Six-pool resources differ: ' + role)
    elif profile is None:
        if (any(source.get('roles', {}).get(role, {}).get('workers') != workers
                for role, workers in (('web', 16), ('student-read', 2), ('teacher-read', 1)))
                or source.get('roles', {}).get('student-read', {}).get('cpus') != 2):
            raise SafetyInterrupted('Legacy worker/resource binding differs')
    else:
        raise SafetyInterrupted('Unknown frozen pool profile')


def validate_pool_budget(content, database_host='school-test-postgres', assessment_size=8):
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read_string(content)
        if assessment_size not in (8, 12):
            raise ValueError()
        pools = {'kt_core': (1, 0), 'kt_auth': (4, 0), 'kt_assessment': (assessment_size, 2),
                 'kt_capture': (4, 1), 'kt_student': (2, 0), 'kt_teacher': (1, 0), 'kt_background': (3, 0)}
        if (set(config['databases']) != set(pools) or config['pgbouncer'].get('pool_mode') != 'transaction'
                or config['pgbouncer'].get('default_pool_size') != '0'):
            raise ValueError()
        for alias, (size, reserve) in pools.items():
            fields = dict(part.split('=', 1) for part in config['databases'][alias].split())
            required = {'host': database_host, 'port': '5432', 'dbname': 'kt_e2e',
                        'user': 'kt_e2e_user', 'pool_size': str(size), 'reserve_pool_size': str(reserve)}
            if 'min_pool_size' in fields:
                required['min_pool_size'] = str(size)
            if fields != required:
                raise ValueError()
    except (configparser.Error, KeyError, ValueError) as error:
        raise SafetyInterrupted('Six-pool PgBouncer aliases, fixed user or connection budget differs') from error


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(name):
    return digest(name)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def write_bridge_start(bridge,value):
    owner=bridge.stat()
    path=bridge/'start.json'
    write_once(path,value)
    if os.name=='posix':os.chown(path,owner.st_uid,owner.st_gid)


class StageGuard:
    def __init__(self):
        self.low_memory_since = None
        self.production_failures = 0
        self.requests = []

    def add_request(self, row):
        at = row.get('completed_at', row.get('sent_at'))
        if not finite(at) or type(row.get('successful')) is not bool:
            raise SafetyInterrupted('Invalid request attempt evidence')
        self.requests.append((at, not row['successful']))

    def update(self, sample, now=None):
        now = time.time() if now is None else now
        if sample.get('memory_available_bytes', 0) < 2 * 1024**3:
            self.low_memory_since = now if self.low_memory_since is None else self.low_memory_since
        else:
            self.low_memory_since = None
        self.production_failures = 0 if sample.get('production_healthy') is True else self.production_failures + 1
        if self.low_memory_since is not None and now - self.low_memory_since >= 15:
            return 'host_memory_below_2_gib_15_seconds'
        if self.production_failures >= 3:
            return 'production_health_failed_three_times'
        if sample.get('disk_error'):
            return 'evidence_disk_error'
        end = int(now // 30) * 30
        windows = [[failed for at, failed in self.requests if left <= at < left + 30]
                   for left in (end - 60, end - 30)]
        self.requests = [(at, failed) for at, failed in self.requests if at >= end - 90]
        if all(rows and sum(rows) / len(rows) > .05 for rows in windows):
            return 'business_failure_two_windows'
        return None


class DrainBudgetExpired(BaseException):
    pass


def bounded_drain(backlog, check_safety, *, clock=time.monotonic, sleep=time.sleep):
    began = clock()
    rows = []
    while True:
        try:
            check_safety()
        except DrainBudgetExpired:
            passed=False
            break
        remaining = 300 - (clock() - began)
        if remaining <= 0:
            passed = False
            break
        try:
            counts = backlog()
        except DrainBudgetExpired:
            passed=False
            break
        rows.append({'elapsed_seconds': clock()-began, 'counts': counts})
        if clock()-began > 300:
            passed = False
            break
        if not any(counts.values()):
            passed = True
            break
        try:
            sleep(min(2, max(0, 300-(clock()-began))))
        except DrainBudgetExpired:
            passed=False
            break
    return {'passed': passed, 'budget_seconds': 300, 'elapsed_seconds': clock()-began, 'samples': rows}


def expected_protocol_counts(plan, login=False):
    selected = [row for row in plan['students'] if row['role'] == 'protocol']
    n = len(selected)
    result = {'login_entry_to_authentication_end': n, 'login_to_home_ready': n}
    if not login:
        result.update(start_to_first_question_ready=n, navigation_next_ready=sum(len(s['excursion_items']) for s in selected),
            navigation_return_ready=sum(len(s['excursion_items']) for s in selected), submit_next_ready=n*19,
            finish_ready=n, save_draft=sum(28+len(s['modify_items']) for s in selected),
            submit_ack=n*19, submit_ack_to_next_ready=n*19, finish_submit_ack=n, finish_ack_to_terminal_ready=n,
            confidence_entry=n*20, confidence_think=n*20, confidence_prompt=n*20)
    return result


def operation_score(rows, expected, thresholds):
    metrics = {}
    complete = all(isinstance(row, dict) and finite(row.get('elapsed_seconds'))
        and row['elapsed_seconds'] >= 0 and type(row.get('successful')) is bool for row in rows)
    for metric, planned in expected.items():
        measured = [row for row in rows if row.get('metric') == metric]
        values = [row['elapsed_seconds'] for row in measured if finite(row.get('elapsed_seconds')) and row['elapsed_seconds'] >= 0]
        aggregate = summary(values)
        failed = sum(row.get('successful') is not True for row in measured)
        reached = len(measured) == planned and len(values) == planned and failed == 0
        bounds = thresholds.get(metric)
        passed = reached and (not planned or bounds is None or (
            aggregate['p95_seconds'] <= bounds[0] and aggregate['p99_seconds'] <= bounds[1]))
        metrics[metric] = {'all_operations': aggregate, 'planned_count': planned, 'attempted_count': len(measured),
            'failed_count': failed, 'not_reached_count': max(0, planned-len(measured)), 'passed': passed}
        complete &= reached
    return metrics, complete, complete and all(row['passed'] for row in metrics.values())


def browser_score(plan, browser, login=False):
    expected = [row for row in plan['students'] if row['role'] == 'browser']
    probes = browser.get('probes', [])
    identities = [row.get('username_sha256') for row in probes]
    complete = len(probes) == 2 and len(set(identities)) == 2 and set(identities) == {identity(s['username']) for s in expected}
    scores = {}
    for student in expected:
        name = identity(student['username'])
        matches = [row for row in probes if row.get('username_sha256') == name]
        if len(matches) != 1:
            complete = False
            continue
        probe = matches[0]
        rows = [{'metric': a.get('action'), 'elapsed_seconds': a.get('duration_ms', math.nan)/1000,
                 'successful': a.get('completed') is True} for a in probe.get('actions', [])]
        drafts = [row for row in probe.get('waterfall', []) if row.get('endpoint', '').split('?',1)[0].endswith('/draft/')]
        rows += [{'metric':'save_draft', 'elapsed_seconds': r.get('duration_ms',math.nan)/1000,
                  'successful':type(r.get('status')) is int and 200 <= r['status'] < 300 and not r.get('failed')} for r in drafts]
        planned = {'login':1, 'task_list':1} if login else {'login':1, 'task_list':1, 'start_first_ready':1,
            'navigation_next_ready':len(student['excursion_items']), 'navigation_return_ready':len(student['excursion_items']),
            'submit_next_ready':19, 'finish_ready':1, 'submit_request_confidence_ready':20,
            'confidence_input_ready':16, 'confidence_skip_ready':4, 'save_draft':len(drafts),
            'answer_input':20, 'answer_input_segment':8, 'answer_modify':len(student['modify_items']),
            'submit_ack':20, 'next_question_ready':19, 'last_session_ready':1}
        metrics, reached, passed = operation_score(rows, planned, BROWSER_THRESHOLDS)
        authentication_posts = [r for r in probe.get('waterfall', []) if r.get('endpoint') == '/api/v1/auth/login/'
            and r.get('method') == 'POST' and type(r.get('status')) is int and 200 <= r['status'] < 300
            and not r.get('failed') and finite(r.get('sent_at'))]
        def expected_discovery(r):
            return (r.get('endpoint') == '/api/v1/auth/session/' and r.get('method') == 'GET'
                and r.get('phase') == 'login' and r.get('status') == 401 and not r.get('failed')
                and finite(r.get('sent_at')) and any(r['sent_at'] < post['sent_at'] for post in authentication_posts))
        discovery_count = sum(expected_discovery(r) for r in probe.get('waterfall', []))
        errors = any(a.get('completed') is not True for a in probe.get('actions', [])) or any(
            (type(r.get('status')) is not int or r['status'] >= 400 or r.get('failed')) and not expected_discovery(r)
            for r in probe.get('waterfall', []))
        valid = (reached and len(drafts)>=20 and not errors and probe.get('completed') is True
            and probe.get('session_completed') is True and probe.get('evidence_complete') is True
            and probe.get('submitted_count') == 20 and probe.get('page_error_count') == 0
            and probe.get('disabled_tools_passed') is True and probe.get('tools_request_count') == 0)
        if login:
            assessment_requests=[row for row in probe.get('waterfall',[]) if re.search(
                r'/student/(?:tasks/[^/]+/(?:start|resume)/|sessions/|attempts/)|/assessment/|/assessment/process/',row.get('endpoint',''))]
            valid=(reached and not errors and probe.get('mode')=='login-only' and probe.get('completed') is True
                and probe.get('evidence_complete') is True and probe.get('page_error_count')==0
                and probe.get('submitted_count')==0 and probe.get('session_completed') is False
                and not assessment_requests and all(row.get('metric') in ('login','task_list') for row in rows))
        scores[name] = {'metrics':metrics, 'workflow_passed':valid, 'performance_passed':valid and passed,
            'expected_auth_discovery_401_count':discovery_count}
        complete &= valid
    return scores, complete, complete and all(row['performance_passed'] for row in scores.values())


def exact_identities(rows, names):
    observed = [row.get('username_sha256') for row in rows]
    return len(rows)==len(names) and len(set(observed))==len(rows) and set(observed)=={identity(name) for name in names}


def browser_clock_bounds(browser):
    calibration=browser.get('clock_calibration',{})
    lower=calibration.get('server_minus_browser_lower_seconds');upper=calibration.get('server_minus_browser_upper_seconds')
    samples=calibration.get('samples',[])
    if not finite(lower) or not finite(upper) or lower>upper or len(samples)!=3:return None
    if len({row.get('sample_id') for row in samples})!=3:return None
    if any(not all(finite(row.get(key)) for key in ('browser_t0','browser_t1','server_epoch')) or row['browser_t1']<row['browser_t0'] for row in samples):return None
    selected=next((row for row in samples if row.get('sample_id')==calibration.get('selected_sample_id')),None)
    if selected is None or not math.isclose(lower,selected['server_epoch']-selected['browser_t1'],abs_tol=1e-8) or not math.isclose(upper,selected['server_epoch']-selected['browser_t0'],abs_tol=1e-8):return None
    if selected['browser_t1']-selected['browser_t0']>min(row['browser_t1']-row['browser_t0'] for row in samples)+1e-8:return None
    return lower,upper


def combined_login_posts(fixture,http,browser):
    posts=list(http.get('first_login_post_attempts',[]))
    bounds=browser_clock_bounds(browser)
    intervals=[(row.get('sent_at'),row.get('sent_at')) for row in posts]
    for probe in browser.get('probes',[]):
        attempts=[row for row in probe.get('waterfall',[]) if row.get('endpoint')=='/api/v1/auth/login/' and row.get('method')=='POST']
        if attempts:
            row=attempts[0]
            posts.append({'username_sha256':probe.get('username_sha256'),'sent_at':row.get('sent_at'),
                'request_id':row.get('request_id'),'successful':type(row.get('status')) is int and 200<=row['status']<300 and not row.get('failed')})
            intervals.append((row['sent_at']+bounds[0],row['sent_at']+bounds[1]) if bounds and finite(row.get('sent_at')) else (None,None))
    identities=exact_identities(posts,fixture['usernames'])
    finite_sends=all(finite(row.get('sent_at')) for row in posts)
    ids=[row.get('request_id') for row in posts]
    request_ids_valid=all(isinstance(value,str) and value for value in ids) and len(set(ids))==len(ids)
    window=max(row['sent_at'] for row in posts)-min(row['sent_at'] for row in posts) if posts and finite_sends else None
    upper_window=max(row[1] for row in intervals)-min(row[0] for row in intervals) if intervals and all(finite(v) for row in intervals for v in row) else None
    successful=sum(row.get('successful') is True for row in posts)
    return {'first_post_count':len(posts),'first_post_success_count':successful,'send_window_seconds':window,
        'send_window_upper_bound_seconds':upper_window,'clock_calibration':browser.get('clock_calibration'),
        'exact_frozen_identities':identities,'request_ids_valid':request_ids_valid,
        'passed':identities and request_ids_valid and successful==180 and finite_sends and bounds is not None and finite(upper_window) and upper_window<=1,
        'posts':posts,'timing_boundary':'Actual first login POST request send timestamps; login-page preparation excluded'}


def protocol_http_errors_acceptable(fixture, plan, http, request_attempts):
    profile = fixture.get('source_config', {}).get('load_evidence_profile')
    if profile is None:
        return http.get('http_error_count') == 0 and http.get('load_evidence_profile') in (None, 'legacy')
    if (profile != LEASE_RECOVERY_PROFILE or http.get('load_evidence_profile') != profile
            or not isinstance(request_attempts, list)):
        return False
    try:
        declared = http['http_error_accounting']
        expected = lease_recovery_accounting(plan, request_attempts, declared['recovery_markers'], http['http_error_count'])
        return (declared == expected and expected['valid'] and expected['unexpected_http_error_count'] == 0
                and http.get('http_request_count') == expected['journal_request_count'])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def read_request_attempts(path):
    try:
        rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()]
        return rows if all(isinstance(row, dict) for row in rows) else None
    except (OSError, ValueError):
        return None


def score_stage(stage, fixture, plan, http, browser, observation, *, persistence, browser_persistence,
                architecture, drain, load_exit_code, observer_exit_code, stop_reason, teacher_low,
                browser_binding_passed,login_session_count=None,request_attempts=None):
    login = stage == 'login180'
    expected = expected_protocol_counts(plan, login)
    protocol_metrics, reached, protocol_perf = operation_score(http.get('operation_measurements', []),expected,THRESHOLDS)
    accounting = http.get('full_operation_accounting', {})
    counts_match = all(accounting.get(key, {}).get('planned_count')==count
        and accounting[key].get('attempted_count')==count and accounting[key].get('successful_count')==count
        and accounting[key].get('failed_count')==0 and accounting[key].get('timeout_count')==0
        and accounting[key].get('not_reached_count')==0 for key,count in expected.items())
    browser_metrics, browser_complete, browser_perf = browser_score(plan,browser,login)
    clock_bounds=browser_clock_bounds(browser)
    calibrated_probes=[{**row,**({key:row[key]+(clock_bounds[1] if key=='ready_at' else clock_bounds[0])
        for key in ('ready_at','completed_at') if finite(row.get(key))} if clock_bounds else {})} for row in browser.get('probes',[])]
    rows = http.get('students', []) + calibrated_probes
    overlap = observed_active_overlap(plan,rows) if not login else {'achieved':False,'observed':0,'expected':0,'seconds':0,'valid_intervals':False,'boundary':'not_applicable_login_only'}
    valid_binding = (http.get('task_id')==fixture['task_id'] and http.get('plan_sha256')==fingerprint(plan)
        and http.get('student_count')==len(fixture['protocol_usernames']) and http.get('mode')==('login-only' if login else 'natural'))
    protocol_workflow = (http.get('workflow_completed') is True and http.get('execution_completed') is True
        and not http.get('errors') and protocol_http_errors_acceptable(fixture, plan, http, request_attempts)
        and http.get('actual_tool_request_count')==0
        and exact_identities(http.get('students',[]),fixture['protocol_usernames'])
        and all(row.get('workflow_completed') is True for row in http.get('students',[])))
    ends = [http.get('ended')] + [row.get('completed_at') for row in browser.get('probes', [])]
    begins = [http.get('started')] + [row.get('started_at',row.get('ready_at')) for row in browser.get('probes', [])]
    covers = all(finite(v) for v in ends+begins) and finite(observation.get('started_at')) and finite(observation.get('ended_at'))
    covers = covers and observation['started_at'] <= min(begins) and observation['ended_at'] >= max(ends)
    if clock_bounds and all(finite(v) for v in ends+begins+[observation.get('started_at'),observation.get('ended_at')]):
        covers=observation['started_at']<=min([http.get('started')]+[row.get('started_at',row.get('ready_at'))+clock_bounds[0] for row in browser.get('probes',[])]) and observation['ended_at']>=max([http.get('ended')]+[row.get('completed_at')+clock_bounds[1] for row in browser.get('probes',[])])
    checks = {'fixture_and_plan_bound':valid_binding,'protocol_workflow_completed':protocol_workflow,
        'all_protocol_operations_reached':reached and counts_match,'browser_workflow_completed':browser_complete,
        'browser_tools_bound':browser_binding_passed,
        'browser_server_clock_bound':clock_bounds is not None,
        'protocol_persistence_passed':login or persistence.get('passed') is True,
        'browser_persistence_passed':login or browser_persistence.get('passed') is True,
        'grading_coverage_export_consistent':login or architecture.get('passed') is True,
        'teacher_low_pressure_passed':login or teacher_low.get('passed') is True,
        'background_drained':drain.get('passed') is True and drain.get('budget_seconds')==300,
        'observer_completed':observer_exit_code==0 and observation.get('state')=='completed',
        'observer_covers_load':covers,'no_oom':observation.get('oom_kills')==0,
        'no_safety_or_workflow_stop':stop_reason is None,'load_process_completed':load_exit_code==0 or (stage in ('baseline10','diagnostic180') and load_exit_code==1),
        'exact_total_identity_count':exact_identities(rows,fixture['usernames']),
        'full_actual_overlap':not (stage.startswith('natural180-') or stage=='diagnostic180') or overlap['achieved']}
    if login:
        combined=combined_login_posts(fixture,http,browser)
        checks.update(all_first_login_posts_observed=combined['first_post_count']==180 and combined['exact_frozen_identities'],
            all_first_login_posts_successful=combined['first_post_success_count']==180,
            login_post_send_window_passed=combined['passed'],login_only_created_zero_assessment_sessions=login_session_count==0)
    for name,value in (('protocol',persistence),('browser',browser_persistence),('architecture',architecture)):
        checks[name+'_known_fact_integrity']=not confirmed_fact_failure(value)
    perf = protocol_perf and browser_perf
    safe = all(checks.values())
    return {'stage':stage,'source_config':fixture['source_config'],'fixture_run_id':fixture['run_id'],
        'task_id':fixture['task_id'],'seed':plan['seed'],'student_count':fixture['student_count'],
        'protocol_students':len(fixture['protocol_usernames']),'browser_students':len(fixture['browser_usernames']),
        'checks':checks,'performance':{'protocol':protocol_metrics,'browser':browser_metrics,'passed':perf},
        'overlap':overlap,'stage_passed':safe and perf,'escalation_allowed':stage!='diagnostic180' and safe and (perf or stage=='baseline10'),
        'single_round_diagnostic':stage=='diagnostic180',
        **({'combined_login':combined} if login else {}),
        'baseline_performance_only_failure_allowed':stage=='baseline10' and safe and not perf,
        'formal_capacity_accepted':False,'load_exit_code':load_exit_code,'stop_reason':stop_reason}


def require_previous(stage, history, source):
    if stage in DIAGNOSTIC_STAGES:
        if source.get('diagnostic_180_authorized') is not True or source.get('phase') != 'candidate':
            raise SafetyInterrupted('Single 180 diagnostic requires explicit candidate authorization')
        return
    six = source.get('pool_profile') == SIX_POOL_PROFILE
    stages = SIX_STAGES if six else STAGES
    if stage not in stages:
        raise ValueError('Unknown stage')
    for previous in stages[:stages.index(stage)]:
        record = history.get(previous,{})
        if record.get('stage')!=previous or record.get('escalation_allowed') is not True:
            raise SafetyInterrupted('Required predecessor has not passed: '+previous)
        bound = record.get('source_config',{})
        keys = ('roles','session_timeout_seconds','diagnostics_mode') if previous=='baseline10' else (
            'source_sha256','frontend_sha256','config_sha256','roles','admission','session_timeout_seconds','diagnostics_mode')
        keys += ('postgres_profile', 'database_host', 'load_evidence_profile')
        if six:
            if previous == 'baseline10':
                validate_transition_source(bound)
                if (bound.get('phase') != 'baseline' or bound.get('pool_profile') is not None
                        or bound.get('host_budget') != HOST_BUDGET or source.get('host_budget') != HOST_BUDGET
                        or bound.get('durability') != DURABILITY or source.get('durability') != DURABILITY
                        or bound.get('workload_profile') != 'natural-180-v1'
                        or source.get('workload_profile') != 'natural-180-v1'):
                    raise SafetyInterrupted('Baseline and six-pool host, durability or workload differs')
                for role in ('redis-admission', 'redis-session'):
                    before = bound.get('roles', {}).get(role, {})
                    after = source.get('roles', {}).get(role, {})
                    if (any(key not in before or key not in after or before[key] != after[key]
                            for key in ('memory_bytes', 'cpus', 'redis_policy'))):
                        raise SafetyInterrupted('Baseline Redis resources or policy differs: ' + role)
                if any(bound.get('roles', {}).get('db', {}).get(key) != source.get('roles', {}).get('db', {}).get(key)
                       for key in ('memory_bytes', 'cpus', 'cpu_shares')):
                    raise SafetyInterrupted('Baseline isolated PostgreSQL resources differ')
                keys = ('session_timeout_seconds', 'diagnostics_mode', 'postgres_profile', 'database_host', 'load_evidence_profile')
            else:
                keys += ('pool_profile', 'host_budget', 'durability', 'workload_profile')
        if previous=='baseline10' and ({k:v for k,v in bound.get('admission',{}).items() if k!='task_ids'}
                != {k:v for k,v in source.get('admission',{}).items() if k!='task_ids'}):
            raise SafetyInterrupted('Baseline admission resources or capacity differs')
        if any(bound.get(key)!=source.get(key) for key in keys):
            raise SafetyInterrupted('Predecessor source, roles or fixed configuration differs: '+previous)


def formal_acceptance(history):
    first, second = (history.get(stage,{}) for stage in ('natural180-r1','natural180-r2'))
    return (all(row.get('stage_passed') is True and row.get('student_count')==180
        and row.get('protocol_students')==178 and row.get('browser_students')==2
        and row.get('overlap',{}).get('achieved') is True for row in (first,second))
        and first.get('seed')==20261013 and second.get('seed')==20261014
        and first.get('task_id')!=second.get('task_id') and first.get('fixture_run_id')!=second.get('fixture_run_id')
        and first.get('source_config')==second.get('source_config'))


class TransitionRuntime(ArchitectureRuntime):
    def __init__(self, config):
        super().__init__(config)
        self.guard = StageGuard()
        self.driver_dir = Path(config['driver_dir'])
        self.history_root = Path(config['history_root'])
        self.bridge_root = Path(config['browser_bridge_dir'])
        source = self.source_value()
        validate_transition_source(source)
        if not config.get('browser_tool_files') or any(not re.fullmatch('[a-f0-9]{64}',v) for v in config['browser_tool_files'].values()):
            raise SafetyInterrupted('Browser tool hashes must be frozen before preparation')
        self.verify_drivers()

    @property
    def runtime_roles(self):
        roles = super().runtime_roles
        return roles if getattr(self,'_without_session',False) else roles+('redis-session',)

    @property
    def observer_script(self):
        return self.driver_dir/'scripts/load/observe_school_runtime.py'

    def verify_drivers(self):
        files = self.config.get('driver_files',{})
        required = {str(self.driver_dir/'scripts/load'/name) for name in (
            'school_natural_load.py','natural_workload.py','observe_school_runtime.py',
            'capture_protocol.py','assessment_300.py','school_short_storm.py','school_synchronized_180.py',
            'school_resource_budget.py','school_mixed_300.py','school_architecture_load.py')}
        required.update(str(Path(__file__).resolve().parent/name) for name in (
            'school_architecture_runtime.py','school_diagnostic_runtime.py','school_diagnostic_sweep.py','school_native_db_probe.py'))
        required.add(str(Path(__file__).resolve()))
        required.add(str(Path(__file__).resolve().with_name('school_worker_short.py')))
        if self.six_pool_profile:
            required.add(str(self.driver_dir / 'scripts/ops/school_capture_priority.py'))
        if not required.issubset(files) or any(not re.fullmatch('[a-f0-9]{64}',value) or sha256(path)!=value for path,value in files.items()):
            raise SafetyInterrupted('Frozen driver hash mismatch or required driver absent')

    def load_command(self, attempt, fixture_id, args):
        command = super().load_command(attempt, fixture_id, args)
        index = command.index('--entrypoint')
        return command[:index]+['-v',str(self.driver_dir/'scripts/load')+':/app/scripts/load:ro']+command[index:]

    def run_private(self,command,attempt,label,timeout=300):
        if command[:2]!=['docker','run'] or '--name' not in command:
            return super().run_private(command,attempt,label,timeout)
        previous=self.load_name
        self.load_name=command[command.index('--name')+1]
        try:
            code=super().run_private(command,attempt,label,timeout)
            if code==124:self.stop_load()
            return code
        except BaseException:
            self.stop_load()
            raise
        finally:
            self.load_name=previous

    def verify_redis_policies(self,roles):
        observed={}
        required={'appendonly','appendfsync','maxmemory-policy','save'}
        for role in ('redis-session','redis-admission'):
            expected=self.source_value()['roles'].get(role,{}).get('redis_policy')
            if not isinstance(expected,dict) or set(expected)!=required or any(type(value) is not str for value in expected.values()):
                raise SafetyInterrupted('Complete frozen Redis policy required: '+role)
            observed[role]={}
            for key,value in expected.items():
                try:
                    reply=json.loads(self.capture(['docker','exec',roles[role]['Id'],'redis-cli','-2','--json','CONFIG','GET',key]))
                except ValueError as error:
                    raise SafetyInterrupted('Redis policy JSON could not be read: '+role) from error
                if reply!=[key,value]:
                    raise SafetyInterrupted('Frozen Redis policy changed: '+role+'/'+key)
                observed[role][key]=reply[1]
        return observed

    def validate_containers(self, items):
        sessions = [row for row in items if row['Config'].get('Labels',{}).get('com.docker.compose.service')=='redis-session']
        if len(sessions)!=1:
            raise SafetyInterrupted('Exactly one isolated session Redis required')
        session = sessions[0]
        expected = self.source_value()['roles']['redis-session']
        if (session['Config']['Labels'].get('com.docker.compose.project')!=PROJECT or not session['State']['Running']
            or session['Image']!=self.config['images']['redis-session'] or session['HostConfig']['Memory']!=expected['memory_bytes']
            or session['HostConfig']['NanoCpus']!=round(expected['cpus']*1e9)
            or session['HostConfig'].get('PortBindings') or set(session['NetworkSettings']['Networks'])!={PROJECT+'_default'}):
            raise SafetyInterrupted('Session Redis identity/network/resources changed')
        self._without_session=True
        try:
            roles=super().validate_containers([row for row in items if row is not session])
        finally:
            self._without_session=False
        roles['redis-session']=session
        for role,item in roles.items():
            expected_env=self.source_value()['roles'].get(role,{}).get('environment',{})
            actual_env=dict(value.split('=',1) for value in item['Config'].get('Env',[]) if '=' in value)
            if any(actual_env.get(key)!=str(value) for key,value in expected_env.items()):
                raise SafetyInterrupted('Frozen pool/runtime environment changed on '+role)
        self.verify_redis_policies(roles)
        for role in self.http_workers:
            env=dict(value.split('=',1) for value in roles[role]['Config'].get('Env',[]) if '=' in value)
            target={'SCHOOL_REDIS_SESSION_ENABLED':'1','CLOSE_BUNDLE_ENABLED':'1',
                'SESSION_REDIS_TIMEOUT_SECONDS':'5','SCHOOL_CAPTURE_DIAGNOSTICS_MODE':'light',
                'ASSESSMENT_TRANSITION_ENABLED':'0' if self.source_value()['phase']=='baseline' else '1'}
            if self.source_value()['phase']=='candidate':
                target['ASSESSMENT_TRANSITION_COURSE_IDS']='*'
            if any(env.get(key)!=value for key,value in target.items()):
                raise SafetyInterrupted('Frozen session/close/diagnostics/transition flags changed on '+role)
            if self.six_pool_profile and env.get('SCHOOL_POOL_PROFILE') != SIX_POOL_PROFILE:
                raise SafetyInterrupted('Frozen pool profile environment differs on ' + role)
        files=self.source_value().get('files',{})
        backend={name.removeprefix('server/'):digest for name,digest in files.items() if name.startswith('server/')}
        if not backend:
            raise SafetyInterrupted('Frozen server source file hashes required')
        script='import hashlib,json,pathlib;expected='+repr(backend)+';print(json.dumps({p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in expected}))'
        backend_roles = tuple(self.http_workers) + ('scheduler', 'analytics-worker', 'student-comparison-worker', 'publication-worker', 'export-worker')
        if self.six_pool_profile and len({roles[role]['Image'] for role in backend_roles}) != 1:
            raise SafetyInterrupted('Six-pool backend roles must use one immutable image')
        for role in backend_roles:
            if json.loads(self.capture(['docker','exec',roles[role]['Id'],'python','-c',script],timeout=60))!=backend:
                raise SafetyInterrupted('Different source version on role '+role)
        if self.six_pool_profile:
            validate_pool_budget(self.capture(['docker', 'exec', roles['pgbouncer']['Id'],
                'cat', '/tmp/pgbouncer-school.ini']), self.database_host,
                self.source_value().get('worker_short_profile', {}).get('assessment_pool_size', 8))
        return roles

    def verify_runtime(self):
        self.verify_drivers()
        super().verify_runtime()
        sql = "SELECT json_build_object('fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit')"
        if self.six_pool_profile:
            sql += ",'full_page_writes',current_setting('full_page_writes')"
        durability=self.db_json(sql + ')')
        if durability != (DURABILITY if self.six_pool_profile else {'fsync':'on','synchronous_commit':'on'}):
            raise SafetyInterrupted('PostgreSQL durability configuration differs')

    def stage_attempt(self, stage, create=False):
        path=self.private/stage
        if create:
            path.mkdir(mode=0o700)
        if path.is_symlink() or not path.resolve().is_relative_to(self.private.resolve()):
            raise SafetyInterrupted('Stage evidence path escaped private root')
        return path

    def seed_stage(self, stage):
        attempt=self.stage_attempt(stage,create=True)
        run='transition-'+hashlib.sha256((self.config['run_id']+':'+stage).encode()).hexdigest()[:20]
        script="""import io,json,secrets,os
from pathlib import Path
from django.core.management import call_command
from django.db import connection
from apps.experiments.models import ClassExperimentTask
assert connection.settings_dict['NAME']=='kt_e2e'
buffer=io.StringIO()
call_command('seed_assessment_load',run_id=RUN,students=COUNT,items=20,password=secrets.token_urlsafe(24),capture_protocol=True,confidence_only=True,feedback_mode='full',stdout=buffer)
f=json.loads(buffer.getvalue())
t=ClassExperimentTask.objects.select_related('experiment').get(pk=f['task_id'])
f.update(database='kt_e2e',course_id=t.experiment.course_id,experiment_id=t.experiment_id,class_id=t.class_group_id,teacher_username=t.experiment.owner.username)
with Path('/attempt/unbound-fixture.json').open('x') as target:json.dump(f,target)
os.chmod('/attempt/unbound-fixture.json',0o600)
""".replace('RUN',repr(run)).replace('COUNT',str(COUNTS[stage]))
        if self.run_private(self.direct_command(attempt,['manage.py','shell','-c',script]),attempt,'seed',600):
            raise RuntimeError('Fresh isolated fixture seed failed')
        return {'stage':stage,'task_id':read(attempt/'unbound-fixture.json')['task_id'],'load_executed':False}

    def prepare_stage(self, stage):
        attempt=self.stage_attempt(stage)
        self.bind_attempt(attempt,create=True)
        fixture=read(attempt/'unbound-fixture.json')
        names=fixture['usernames']
        fixture.update(source_config=self.source_value(),browser_usernames=names[:2],
            protocol_usernames=names[2:],load_mode='login-only' if stage=='login180' else 'natural',workload_version='natural-180-v1')
        if fixture.get('scenario')!=CONFIDENCE_ONLY or fixture['student_count']!=COUNTS[stage] or fixture['item_count']!=20:
            raise SafetyInterrupted('Confidence-only twenty-item fixture required')
        if fixture['task_id'] not in self.source_value()['admission']['task_ids']:
            raise SafetyInterrupted('Fresh task is absent from frozen admission allowlist')
        plan=build_workload(fixture,SEEDS[stage])
        write_once(attempt/'fixture.json',fixture)
        write_once(attempt/'plan.json',plan)
        binding={'source_config_sha256':fingerprint(self.source_value()),'fixture_sha256':sha256(attempt/'fixture.json'),
            'plan_sha256':sha256(attempt/'plan.json'),'driver_files':self.config['driver_files'],
            'browser_tool_files':self.config['browser_tool_files'],'teacher_probe_sha256':self.config.get('teacher_probe_sha256')}
        write_once(attempt/'fixture-binding.json',binding)
        bridge=self.bridge_root/stage
        bridge.mkdir(mode=0o700)
        for name in ('fixture.json','plan.json','fixture-binding.json'):
            with (bridge/name).open('xb') as stream:stream.write((attempt/name).read_bytes())
            (bridge/name).chmod(0o600)
        return {'stage':stage,'task_id':fixture['task_id'],'fixture_prepared':True,'load_executed':False,'browser_bridge':str(bridge)}

    def history(self):
        return {stage:read(self.history_root/(stage+'.json')) for stage in STAGES + DIAGNOSTIC_STAGES if (self.history_root/(stage+'.json')).exists()}

    def assert_prepared(self,stage,attempt):
        self.bind_attempt(attempt)
        bound=read(attempt/'fixture-binding.json')
        expected={'source_config_sha256':fingerprint(self.source_value()),'fixture_sha256':sha256(attempt/'fixture.json'),
            'plan_sha256':sha256(attempt/'plan.json'),'driver_files':self.config['driver_files'],
            'browser_tool_files':self.config['browser_tool_files'],'teacher_probe_sha256':self.config.get('teacher_probe_sha256')}
        if bound!=expected:
            raise SafetyInterrupted('Source/frontend/config/fixture/plan/driver/tool binding changed')
        fixture=read(attempt/'fixture.json')
        plan=validate_workload(fixture,read(attempt/'plan.json'))
        if plan['seed']!=SEEDS[stage] or fixture['student_count']!=COUNTS[stage] or fixture['source_config']!=self.source_value():
            raise SafetyInterrupted('Wrong stage fixture or source variant')
        if self.source_value()['phase']!=('baseline' if stage=='baseline10' else 'candidate'):
            raise SafetyInterrupted('Wrong deployed variant for stage')
        history=self.history()
        require_previous(stage,history,self.source_value())
        if any(row.get('task_id')==fixture['task_id'] or row.get('fixture_run_id')==fixture['run_id'] for row in history.values()):
            raise SafetyInterrupted('Task or fixture run was already consumed')
        return fixture,plan

    def drain(self,attempt,task_id):
        def safety():
            reason=self.guard.update(self.safety_sample(attempt))
            if reason:raise SafetyInterrupted(reason)
        def expired(signum,frame):raise DrainBudgetExpired()
        previous=signal.signal(signal.SIGALRM,expired)
        signal.alarm(300)
        try:
            result=bounded_drain(lambda:self.backlog(task_id),safety)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM,previous)
        write_once(attempt/'drain.json',result)
        return result

    def teacher_command(self,attempt,mode):
        path=Path(self.config['teacher_probe_path'])
        if sha256(path)!=self.config.get('teacher_probe_sha256'):
            raise SafetyInterrupted('Teacher verifier hash changed')
        destination=attempt/'teacher_probe.py'
        if not destination.exists():
            with destination.open('xb') as stream:stream.write(path.read_bytes())
            destination.chmod(0o600)
        return self.load_command(attempt,self.config['run_id']+'-'+attempt.name+'-teacher-'+mode,
            ['/attempt/teacher_probe.py','--mode',mode,'--output','/attempt/teacher-'+mode+'.json'])

    def collect_persistence(self,stage,attempt,fixture,browser):
        if stage=='login180':return {},{},{}
        commands=(('persistence','verify_school_protocol_load',['--workload-version','natural-180-v1','--plan','/attempt/plan.json']),
                  ('browser-persistence','verify_school_browser_witness',['--browser-report','/attempt/browser.json','--plan','/attempt/plan.json']))
        outputs=[]
        for label,command,extra in commands:
            code=self.run_private(self.direct_command(attempt,['manage.py',command,'--manifest','/attempt/fixture.json',
                '--output','/attempt/'+label+'.json',*extra]),attempt,label,240)
            value,error=read_evidence(attempt/(label+'.json'))
            if error or code:value={**value,'passed':False,'verification_exit_code':code,'evidence_error':error}
            outputs.append(value)
        ledger,error=read_evidence(attempt/'fixture.ledger.json')
        if error:
            architecture={'passed':False,'reason':'protocol_ledger_missing'}
        else:
            ledger['sessions'].update(browser.get('architecture_sessions',{}))
            ledger['incomplete']=not(browser.get('completed') is True and not ledger.get('incomplete'))
            with (attempt/'full-fixture.json').open('xb') as stream:stream.write((attempt/'fixture.json').read_bytes())
            (attempt/'full-fixture.json').chmod(0o600)
            write_once(attempt/'full-fixture.ledger.json',ledger)
            code=self.run_private(self.direct_command(attempt,['manage.py','verify_school_architecture_results',
                '--manifest','/attempt/full-fixture.json','--output','/attempt/architecture-results.json']),attempt,'architecture-results',240)
            architecture,error=read_evidence(attempt/'architecture-results.json')
            if code or error:architecture={**architecture,'passed':False,'verification_exit_code':code,'evidence_error':error}
        return *outputs,architecture

    def release_armed_start(self, attempt, bridge, fixture, process):
        deadline = time.monotonic() + 180
        binding = read(attempt/'fixture-binding.json') if (attempt/'fixture-binding.json').exists() else {}
        while time.monotonic() < deadline:
            if (bridge/'browser-launch-error.json').exists():
                raise SafetyInterrupted('Browser failed before common start')
            if process.poll() is not None:
                raise SafetyInterrupted('Protocol driver exited before common start')
            protocol, _ = read_evidence(attempt/'protocol-armed.json')
            browser, _ = read_evidence(bridge/'browser-armed.json')
            if armed_matches(protocol, browser, binding, len(fixture['protocol_usernames'])):
                start_at = time.time() + 15
                release = {'stage': attempt.name, 'start_at': start_at, 'fixture_binding': binding}
                write_once(attempt/'start.json', release)
                write_once(attempt/'started.json', {'started_at': time.time(), 'start_at': start_at,
                                                    'stage': attempt.name, 'both_drivers_armed': True})
                write_bridge_start(bridge, release)
                return start_at
            time.sleep(.2)
        raise SafetyInterrupted('Both browser contexts and protocol driver did not arm within 180 seconds')

    def collect_browser_reports(self, attempt, bridge, *, wait_for_final=False):
        if wait_for_final:
            deadline = time.monotonic() + 40
            while not (bridge/'browser.json').exists() and time.monotonic() < deadline:
                if (bridge/'browser-launch-error.json').exists():
                    break
                time.sleep(.2)
        for name in ('browser.json', 'browser.progress.json', 'browser-binding.json'):
            if (bridge/name).exists():
                with (attempt/name).open('xb') as stream:
                    stream.write((bridge/name).read_bytes())
                (attempt/name).chmod(0o600)
        return (attempt/'browser.json').exists()

    def run_stage(self,stage,start_at):
        short_policy = self.source_value().get('worker_short_profile')
        coordinated = short_policy is not None
        if coordinated and stage not in DIAGNOSTIC_STAGES:
            raise SafetyInterrupted('Worker short policy cannot replace a formal capacity stage')
        if not coordinated and (not finite(start_at) or start_at<time.time()+5 or start_at>time.time()+300):
            raise ValueError('Common browser/protocol start must be five to 300 seconds in the future')
        attempt=self.stage_attempt(stage)
        fixture,plan=self.assert_prepared(stage,attempt)
        self.verify_runtime()
        self.verify_gateway_routing(attempt)
        if self.db_json('SELECT to_json(count(*)) FROM assessment_experimentsession WHERE class_task_id='+str(int(fixture['task_id'])))!=0:
            raise SafetyInterrupted('Fresh task already contains sessions')
        bridge=self.bridge_root/stage
        if any((bridge/name).exists() for name in ('browser.json','browser.progress.json','browser.stop','start.json')):
            raise SafetyInterrupted('Browser bridge already consumed')
        if not coordinated:
            write_once(attempt/'started.json',{'started_at':time.time(),'start_at':start_at,'stage':stage})
            write_bridge_start(bridge,{'stage':stage,'start_at':start_at,'fixture_binding':read(attempt/'fixture-binding.json')})
        logs=[]
        def spawn(command,label):
            log=(attempt/(label+'.log')).open('x',encoding='utf-8');os.chmod(attempt/(label+'.log'),0o600)
            logs.append(log)
            child=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            self.children.append(child)
            return child
        observer=spawn([sys.executable,str(self.observer_script),'--project',PROJECT,'--database-container',self.database_container,
            '--output',str(attempt/'observation.json'),'--duration','3060','--interval','2' if coordinated else '5',
            '--stop-file',str(attempt/'observer.stop')],'observer')
        self.observer=observer
        teacher_runtime=None
        process=None
        teacher=None
        stop_reason=None
        load_exit=None
        began=time.time()
        offset=0
        all_entered = False
        try:
            time.sleep(2)
            if observer.poll() is not None:raise SafetyInterrupted('Observer exited before load')
            command=self.load_command(attempt,self.config['run_id']+'-'+stage,
                ['scripts/load/school_natural_load.py','--manifest','/attempt/fixture.json','--plan','/attempt/plan.json',
                 '--output','/attempt/http.json','--mode','login-only' if stage=='login180' else 'natural',
                 '--base-url',BASE_URL,'--start-at',str(0 if coordinated else start_at),'--timeout','35','--total-budget-seconds','2700',
                 '--stop-file','/attempt/load.stop'])
            if coordinated:
                command += ['--armed-file', '/attempt/protocol-armed.json', '--start-file', '/attempt/start.json']
            self.load_name=command[command.index('--name')+1]
            process=spawn(command,'load');self.http_launched=True
            if coordinated:
                start_at = self.release_armed_start(attempt, bridge, fixture, process)
            if stage!='login180':
                teacher_runtime=TransitionRuntime(self.config)
                teacher_command=self.teacher_command(attempt,'monitor')
                teacher_runtime.load_name=teacher_command[teacher_command.index('--name')+1]
                teacher=spawn(teacher_command,'teacher-monitor')
            while True:
                tick=time.time()
                sample=self.safety_sample(attempt)
                with (attempt/'safety.jsonl').open('a',encoding='utf-8') as stream:
                    os.chmod(attempt/'safety.jsonl',0o600);stream.write(json.dumps(sample)+'\n')
                journal=attempt/'http.attempts.jsonl'
                if journal.exists():
                    with journal.open(encoding='utf-8') as stream:
                        stream.seek(offset)
                        while True:
                            position=stream.tell();line=stream.readline()
                            if not line:break
                            try:row=json.loads(line)
                            except ValueError:stream.seek(position);break
                            self.guard.add_request(row)
                        offset=stream.tell()
                stop_reason=self.guard.update(sample,tick)
                if observer.poll() is not None:stop_reason=stop_reason or 'observer_failed_or_ended_early'
                if teacher is not None and teacher.poll() is not None:
                    stop_reason=stop_reason or 'teacher_monitor_ended_early'
                if tick>=start_at+(300 if stage=='login180' else 2700):stop_reason=stop_reason or 'stage_budget'
                progress,_=read_evidence(attempt/'http.progress.json')
                browser_progress,_=read_evidence(bridge/'browser.progress.json')
                if progress.get('errors'):stop_reason=stop_reason or 'unrecovered_protocol_workflow_error'
                if (bridge/'browser-launch-error.json').exists():
                    stop_reason=stop_reason or 'browser_launcher_failed'
                if any(row.get('failure_stage') for row in browser_progress.get('probes',[])):
                    stop_reason=stop_reason or 'unrecovered_browser_workflow_error'
                protocol_done=process.poll() is not None
                browser_done=(bridge/'browser.json').exists()
                if protocol_done:
                    completed,_=read_evidence(attempt/'http.json')
                    if protocol_finished_error(completed, len(fixture['protocol_usernames'])):
                        stop_reason=stop_reason or 'unrecovered_protocol_workflow_error'
                if browser_done:
                    completed,_=read_evidence(bridge/'browser.json')
                    if completed.get('completed') is not True:stop_reason=stop_reason or 'unrecovered_browser_workflow_error'
                if coordinated:
                    students, _ = read_evidence(attempt/'http.students.json')
                    protocol_ready = sum(bool(row.get('ready_at')) for row in students.get('students', []))
                    browser_ready = sum(bool(row.get('ready_at')) for row in browser_progress.get('probes', []))
                    if protocol_ready == len(fixture['protocol_usernames']) and browser_ready == 2:
                        if not all_entered:
                            write_once(attempt/'all-entered.json', {'observed_at': tick, 'start_at': start_at,
                                'protocol_ready': protocol_ready, 'browser_ready': browser_ready})
                        all_entered = True
                    if stage == 'diagnostic2':
                        probes = browser_progress.get('probes', [])
                        if len(probes) == 2 and all(row.get('submitted_count', 0) >= 1 for row in probes):
                            stop_reason = stop_reason or 'short_connectivity_complete'
                    stop_reason = stop_reason or short_stop_reason(short_policy, start_at, tick,
                                                                   protocol_ready, browser_ready, all_entered)
                    value = {'observed_at': tick, 'start_at': start_at, 'workers': short_policy['workers'],
                             'protocol_ready': protocol_ready, 'browser_ready': browser_ready,
                             'all_entered': all_entered, 'stop_reason': stop_reason}
                    temporary = bridge/'operator-progress.tmp'
                    temporary.write_text(json.dumps(value)); temporary.chmod(0o600)
                    if os.name == 'posix':
                        os.chown(temporary, bridge.stat().st_uid, bridge.stat().st_gid)
                    temporary.replace(bridge/'operator-progress.json')
                if stop_reason or(protocol_done and browser_done):break
                time.sleep(max(0,2-(time.time()-tick)))
        except BaseException as error:
            stop_reason=stop_reason or 'coordinator_exception'
            if coordinated and not (attempt/'first-failure.json').exists():
                write_once(attempt/'first-failure.json', {'stop_reason': stop_reason,
                    'error_type': type(error).__name__, 'at': time.time()})
            raise
        finally:
            if coordinated and stop_reason and not (attempt/'first-failure.json').exists():
                write_once(attempt/'first-failure.json', {'stop_reason': stop_reason, 'at': time.time()})
            if stop_reason:
                (attempt/'load.stop').touch(mode=0o600);(bridge/'browser.stop').touch(mode=0o600)
            if process is not None:
                try:process.wait(timeout=40)
                except subprocess.TimeoutExpired:self.stop_load();self.terminate_child(process)
                load_exit=process.returncode
            if teacher is not None:
                (attempt/'teacher.stop').touch(mode=0o600)
                try:teacher.wait(timeout=40)
                except subprocess.TimeoutExpired:teacher_runtime.stop_load();self.terminate_child(teacher)
            if not coordinated:
                self.collect_browser_reports(attempt, bridge)
            try:drain=self.drain(attempt,fixture['task_id'])
            except BaseException as error:
                if not coordinated:
                    raise
                drain = {'passed': False, 'budget_seconds': 300, 'error_type': type(error).__name__}
                write_once(attempt/'cleanup-error.json', drain)
            finally:
                if coordinated:
                    self.collect_browser_reports(attempt, bridge, wait_for_final=True)
                (attempt/'observer.stop').touch(mode=0o600)
                try:observer.wait(timeout=60)
                except subprocess.TimeoutExpired:self.terminate_child(observer)
                for log in logs:log.close()
        ended=time.time()
        http,_=read_evidence(attempt/'http.json');browser,_=read_evidence(attempt/'browser.json')
        if coordinated:
            persistence = browser_persistence = architecture = {'passed': False,
                'status': 'short_diagnostic_incomplete', 'raw_fact_integrity_failed': False}
        else:
            persistence,browser_persistence,architecture=self.collect_persistence(stage,attempt,fixture,browser)
        teacher_low={}
        if stage!='login180' and stop_reason is None and drain['passed']:
            teacher_code=self.run_private(self.teacher_command(attempt,'low'),attempt,'teacher-low',420)
            teacher_low,error=read_evidence(attempt/'teacher-low.json')
            if teacher_code or error:teacher_low={**teacher_low,'passed':False,'verification_exit_code':teacher_code}
        observation,_=read_evidence(attempt/'observation.json')
        browser_binding,_=read_evidence(attempt/'browser-binding.json')
        binding_passed=browser_binding==read(attempt/'fixture-binding.json')
        result=score_stage(stage,fixture,plan,http,browser,observation,persistence=persistence,
            browser_persistence=browser_persistence,architecture=architecture,drain=drain,load_exit_code=load_exit,
            observer_exit_code=observer.returncode,stop_reason=stop_reason,teacher_low=teacher_low,
            browser_binding_passed=binding_passed,login_session_count=self.db_json('SELECT to_json(count(*)) FROM assessment_experimentsession WHERE class_task_id='+str(int(fixture['task_id']))) if stage=='login180' else None,
            request_attempts=read_request_attempts(attempt/'http.attempts.jsonl'))
        result.update(attempt_dir=str(attempt),started_at=began,ended_at=ended,stage_elapsed_seconds=ended-began,
            workload_budget_seconds=300 if stage=='login180' else 2700,drain_budget_seconds=300,
            confidence_feedback_timing_boundary='Synthetic confidence thinking and feedback remain separate from operation latency')
        if coordinated:
            result.update(short_diagnostic=True, worker_short_profile=short_policy, common_start_at=start_at,
                          all_entered=all_entered, formal_capacity_accepted=False, escalation_allowed=False,
                          workload_budget_seconds=short_policy['duration_seconds'], stage_passed=False)
        self.run_private(self.compose+['logs','--no-color','--since',str(int(began)),*self.runtime_roles],attempt,'services',60)
        write_once(attempt/'slow-operations.json',slow_operations(http,browser,attempt))
        history=self.history();history[stage]=result
        result['formal_capacity_accepted']=stage not in DIAGNOSTIC_STAGES and not coordinated and formal_acceptance(history)
        write_once(attempt/'result.json',result)
        self.history_root.mkdir(mode=0o700,parents=True,exist_ok=True)
        write_once(self.history_root/(stage+'.json'),result)
        return result


def slow_operations(http,browser,attempt):
    operations=[{'kind':'protocol',**row} for row in http.get('operation_measurements',[]) if row.get('elapsed_seconds',0)>3]
    requests=[]
    journal=attempt/'http.attempts.jsonl'
    attempts=[]
    if journal.exists():
        for line in journal.read_text().splitlines():
            row=json.loads(line)
            attempts.append(row)
            if row.get('elapsed_seconds',0)>3:requests.append({'kind':'protocol',**row})
    arrivals=http.get('item_arrivals',[])
    for operation in operations:
        student=operation.get('student_index')
        item=operation.get('item_index')
        anchors=sorted([row for row in arrivals if row.get('student_index')==student],key=lambda row:row['item_index'])
        current=next((row for row in anchors if row['item_index']==item),None)
        following=next((row for row in anchors if item is not None and row['item_index']>item),None)
        outcome=next((row for row in http.get('students',[]) if row.get('student_index')==student),{})
        beginning=current.get('ready_at') if current else outcome.get('actual_arrival_at')
        end=following.get('ready_at') if following else outcome.get('completed_at',outcome.get('home_ready_at'))
        selected=[row for row in attempts if row.get('student_index')==student and finite(beginning)
            and finite(end) and beginning<=row.get('sent_at',-math.inf)<=end]
        operation['request_ids']=sorted({row['request_id'] for row in selected if row.get('request_id')})
        operation['correlation_scope']='Same frozen student/item interval; neighboring operations can share this interval'
        requests += [{'kind':'protocol',**row} for row in selected]
    for probe in browser.get('probes',[]):
        for action in probe.get('actions',[]):
            if action.get('duration_ms',0)<=3000:continue
            selected=[row for row in probe.get('waterfall',[]) if finite(row.get('start_ms'))
                and action['start_ms']<=row.get('end_ms',row['start_ms'])
                and row['start_ms']<=action['start_ms']+action['duration_ms']]
            operations.append({'kind':'browser','username_sha256':probe.get('username_sha256'),**action,
                'request_ids':sorted({row['request_id'] for row in selected if row.get('request_id')}),
                'correlation_scope':'Observed overlapping browser action/request intervals'})
            requests += [{'kind':'browser','username_sha256':probe.get('username_sha256'),**row} for row in selected]
        requests += [{'kind':'browser','username_sha256':probe.get('username_sha256'),**row}
            for row in probe.get('waterfall',[]) if row.get('duration_ms',0)>3000]
    unique={fingerprint(row):row for row in requests}
    requests=list(unique.values())
    ids={row['request_id'] for row in requests if row.get('request_id')}
    traces=[]
    service_log=attempt/'services.log'
    if service_log.exists():
        for line in service_log.read_text().splitlines():
            start=line.find('{')
            if start<0:continue
            try:row=json.JSONDecoder().raw_decode(line[start:])[0]
            except ValueError:continue
            if isinstance(row,dict) and row.get('request_id') in ids:traces.append(row)
    return {'selection':'All individual operations and requests over three seconds; failed attempts retained',
        'operations':operations,'requests':requests,'server_traces':traces,
        'unmatched_request_ids':sorted(ids-{row['request_id'] for row in traces})}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--stage',choices=STAGES + DIAGNOSTIC_STAGES,required=True)
    commands=parser.add_mutually_exclusive_group(required=True)
    commands.add_argument('--seed-only',action='store_true')
    commands.add_argument('--prepare-only',action='store_true')
    commands.add_argument('--start-at',type=float)
    args=parser.parse_args()
    if os.name!='posix' or os.geteuid()!=0:parser.error('Authenticated root-owned Linux runtime required')
    if args.config.is_symlink() or args.config.stat().st_uid!=0 or args.config.stat().st_mode&0o077:
        parser.error('Root-private runtime config required')
    runtime=TransitionRuntime(read(args.config))
    def interrupted(signum,frame):raise SafetyInterrupted('Coordinator interrupted')
    signal.signal(signal.SIGINT,interrupted);signal.signal(signal.SIGTERM,interrupted)
    try:
        if args.seed_only:result=runtime.seed_stage(args.stage)
        elif args.prepare_only:result=runtime.prepare_stage(args.stage)
        else:result=runtime.run_stage(args.stage,args.start_at)
        print(json.dumps(result))
        return 0 if args.seed_only or args.prepare_only or result['escalation_allowed'] else 1
    except SafetyInterrupted as error:
        attempt=runtime.private/args.stage
        if attempt.exists() and not(attempt/'safety-interrupted.json').exists():
            write_once(attempt/'safety-interrupted.json',{'reason':str(error),'at':time.time(),'stage':args.stage})
        print(json.dumps({'stage':args.stage,'safety_interrupted':True,'reason':str(error),'formal_capacity_accepted':False}))
        return 75
    finally:
        runtime.stop_load()
        for child in runtime.children:runtime.terminate_child(child)
        if runtime.native_probe is not None:runtime.native_probe.close()


if __name__=='__main__':sys.exit(main())
