"""Owned 50/180 retest identity, phase budgets and evidence-driven configuration steps."""
import copy
import json
import math
import os
from pathlib import Path, PurePosixPath

MANIFEST_ENV = 'SCHOOL_CAPACITY_RUN_MANIFEST'
PROJECT = 'kt-school-e2e-capacity-retest-20261016'
ROOT = '/home/ktdata/student-capacity-tests/20261016-retest'
PORT = 18085
LEGACY_MODES = ('login', 'startup', 'login-mixed', 'startup-mixed', 'entry-mixed',
         'save-only', 'full-flow', 'submission', 'submission-mixed')
MODES = ('full-flow',)
WORKLOAD_PROFILE = 'full-flow-20q-gradual-login-v1'
HISTORICAL_PROFILE = 'historical-v3'
HTTP_WORKERS = {'entry': 'ASSESSMENT_ENTRY_WORKERS', 'assessment': 'ASSESSMENT_REMAINING_WORKERS',
                'capture': 'ASSESSMENT_CAPTURE_WORKERS', 'web': 'ASSESSMENT_CORE_WORKERS',
                'teacher-read': 'ASSESSMENT_TEACHER_WORKERS'}
POOL_KEYS = ('ENTRY', 'REMAINING', 'CAPTURE', 'CORE', 'TEACHER', 'BACKGROUND', 'SUBMISSION')
MEASUREMENT_VERSION = 'measurement-v5-gradual-login-r6'
METRIC_SCHEMA_VERSION = 'student-capacity-metrics-v3'
LIMITS = {'login': {'p95_ms': 10000, 'p99_ms': 20000},
          'startup': {'p95_ms': 5000, 'p99_ms': 10000},
          'save_scheduled': {'p95_ms': 1000, 'p99_ms': 3000},
          'navigation': {'p95_ms': 1000, 'p99_ms': 3000},
          'submission': {'p95_ms': 1000, 'p99_ms': 3000},
          'processing': {'p95_ms': 60000, 'max_ms': 300000},
          'statistics': {'max_ms': 300000}}
DIMENSION_MODES = {
    'assessment': ('save-only', 'login-mixed', 'startup-mixed', 'entry-mixed', 'submission', 'submission-mixed', 'full-flow'),
    'capture': ('save-only', 'startup', 'startup-mixed', 'entry-mixed', 'submission-mixed', 'full-flow'),
    'entry': ('login', 'startup', 'login-mixed', 'startup-mixed', 'entry-mixed', 'full-flow'),
    'login': ('login', 'login-mixed', 'entry-mixed', 'full-flow'),
    'startup': ('startup', 'startup-mixed', 'entry-mixed', 'full-flow'),
    'submission': ('submission', 'submission-mixed', 'save-only', 'full-flow'),
    'statistics': ('submission-mixed', 'submission', 'save-only', 'full-flow')}


def dimension_family(dimension):
    if dimension in ('burst', 'rate'):
        return 'startup'
    if dimension == 'heavy-limit':
        return 'entry'
    return dimension.split('-')[0]


def affected_modes(dimension, measured):
    supported = DIMENSION_MODES.get(dimension_family(dimension), MODES)
    return tuple(mode for mode in supported if mode in measured) or supported[:1]


def compare_candidate(reference, candidate, dimension):
    """Reject missing populations and individual SLA/tail regressions."""
    old = {(row['count'], row['mode']): row for row in reference}
    new = {(row['count'], row['mode']): row for row in candidate}
    issues, comparisons, gains = [], [], 0
    if set(old) != set(new) or not old or {key[0] for key in old} != {50, 180}:
        return {'improved': False, 'issues': ['paired_50_180_populations_required'], 'metrics': []}
    targets = {'assessment': ('save_scheduled', 'navigation', 'submission'),
               'capture': ('save_scheduled', 'navigation', 'startup'), 'entry': ('login', 'startup'),
               'login': ('login',), 'startup': ('startup',), 'submission': ('processing',),
               'statistics': ('statistics',)}.get(dimension_family(dimension), ())
    for key, baseline in old.items():
        trial = new[key]
        if (trial.get('safety_stop') or any(trial.get(field) is not True for field in
                ('generator_valid', 'monitor_ok', 'integrity_passed', 'full_denominator_completed'))):
            issues.append('candidate_evidence_invalid')
        for metric, limits in LIMITS.items():
            before, after = baseline.get('timings', {}).get(metric) or {}, trial.get('timings', {}).get(metric) or {}
            if not before.get('planned'):
                continue
            population_changed = before['planned'] != after.get('planned')
            if (after.get('observed', after.get('planned')) != after.get('planned')
                    or (population_changed and metric != 'statistics')):
                issues.append('metric_population_incomplete:' + metric)
            for percentile, limit in limits.items():
                a, b = before.get(percentile), after.get(percentile)
                decision = 'unchanged'
                if b is None:
                    issues.append('metric_missing:' + metric + ':' + percentile)
                elif a is None or population_changed:
                    if b <= limit and (population_changed or before.get('missing', 0) > 0):
                        decision = 'reached_sla_from_incomplete_baseline' if a is None else 'within_sla_changed_population'
                        gains += int(metric in targets)
                    else:
                        issues.append('reference_metric_unresolved:' + metric + ':' + percentile)
                elif a <= limit and b > limit:
                    issues.append('passed_metric_regressed:' + metric + ':' + percentile)
                elif a > limit and b > a * 1.10:
                    issues.append('unpassed_metric_regressed_over_10_percent:' + metric + ':' + percentile)
                elif metric in targets and a > limit:
                    if b <= limit or b <= a * .95:
                        gains += 1
                        decision = 'improved'
                    else:
                        issues.append('target_gain_below_5_percent:' + metric + ':' + percentile)
                comparisons.append({'count': key[0], 'mode': key[1], 'metric': metric, 'percentile': percentile,
                    'before_ms': a, 'after_ms': b, 'limit_ms': limit, 'decision': decision,
                    'change_fraction': (b / a - 1) if not population_changed and isinstance(a, (int, float))
                        and a > 0 and isinstance(b, (int, float)) else None})
    return {'improved': not issues and (gains > 0 or all(row.get('passed') for row in candidate)),
            'issues': sorted(set(issues)), 'metrics': comparisons}


def metric_statistics(values, *, limit=None, failed=0, definition=None):
    """Nearest-rank over the planned population, including unobserved actions."""
    ordered = sorted(value if isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value) and value >= 0 else math.inf for value in values)
    observed = sum(math.isfinite(value) for value in ordered)
    def quantile(probability):
        value = ordered[max(0, math.ceil(len(ordered) * probability) - 1)] if ordered else math.inf
        return value if math.isfinite(value) else None
    return {'planned': len(ordered), 'observed': observed, 'missing': len(ordered) - observed,
            'failed': failed, 'within_sla': sum(value <= limit for value in ordered) if limit is not None else None,
            **{key + '_ms': quantile(probability) for key, probability in
               (('p50', .5), ('p90', .9), ('p95', .95), ('p99', .99), ('max', 1))},
            'unit': 'ms', 'definition': definition,
            'validity': 'not_applicable' if not ordered else 'complete' if observed == len(ordered) else 'missing'}


def r0_parameters():
    values = {
        'SCHOOL_POOL_PROFILE': 'shared-entry-v3', 'SCHOOL_REDIS_SESSION_ENABLED': 0,
        'ASSESSMENT_ENTRY_WORKERS': 15, 'ASSESSMENT_ENTRY_DB_CONNECTIONS': 11,
        'ASSESSMENT_REMAINING_WORKERS': 12, 'ASSESSMENT_REMAINING_DB_CONNECTIONS': 8,
        'ASSESSMENT_CAPTURE_WORKERS': 3, 'ASSESSMENT_CAPTURE_DB_CONNECTIONS': 3,
        'ASSESSMENT_CORE_WORKERS': 1, 'ASSESSMENT_CORE_DB_CONNECTIONS': 1,
        'ASSESSMENT_TEACHER_WORKERS': 1, 'ASSESSMENT_TEACHER_DB_CONNECTIONS': 1,
        'ASSESSMENT_SUBMISSION_WORKERS': 1, 'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': 1,
        'ASSESSMENT_ANALYTICS_WORKERS': 1, 'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': 2,
        'ASSESSMENT_ENTRY_LOGIN_LIMIT': 12, 'ASSESSMENT_ENTRY_STARTUP_LIMIT': 8,
        'ASSESSMENT_ENTRY_HEAVY_LIMIT': 14, 'ASSESSMENT_ADMISSION_CAPACITY': 8,
        'ASSESSMENT_ENTRY_STARTUP_RATE': 64, 'ASSESSMENT_ADMISSION_RATE_PER_SECOND': 64,
        'ASSESSMENT_ENTRY_STARTUP_BURST': 8, 'SCHOOL_DB_MAX_CONNECTIONS': 64,
        'SCHOOL_DB_RESERVED_CONNECTIONS': 3, 'SCHOOL_DB_DIRECT_CONNECTIONS': 1,
        'SCHOOL_DB_MONITOR_CONNECTIONS': 2, 'SCHOOL_DB_MAINTENANCE_CONNECTIONS': 2,
        'SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS': 0, 'PGBOUNCER_MAX_CLIENT_CONN': 200,
        'DATABASE_CONN_MAX_AGE': 0, 'SCHOOL_HTTP_CONN_MAX_AGE': 0,
    }
    values.update({'ASSESSMENT_' + key + '_DB_RESERVE_CONNECTIONS':
                   2 if key == 'REMAINING' else 1 if key == 'CAPTURE' else 0 for key in POOL_KEYS})
    return {key: str(value) for key, value in values.items()}


def default_manifest():
    return {'version': 'student-capacity-retest-v1', 'project': PROJECT, 'root': ROOT, 'port': PORT,
            'database': 'kt_e2e', 'counts': [50, 180], 'pressure_driver': 'external_windows',
            'workload_profile': WORKLOAD_PROFILE,
            'browser_drivers': ['desktop', 'mobile_emulation'], 'r0': r0_parameters(),
            'source_archive_sha256': '1e3ee222e2bfcd449e3f1d8460ee1129eecd11057a055d64bf8c712824fd644d',
            'source_fixture': 'evidence/matrix-smoke1/cells/m3563149ed2fa1776d1a4/fixture.json',
            'baseline_id': 'retest20261016', 'phase_bounds': phase_bounds('paper-20q')}


def validate_manifest(value):
    if (value.get('version') != 'student-capacity-retest-v1' or value.get('project') != PROJECT
            or value.get('root') != ROOT or value.get('port') != PORT
            or value.get('database') != 'kt_e2e' or value.get('counts') != [50, 180]
            or value.get('pressure_driver') != 'external_windows'):
        raise ValueError('Only the explicitly authorized new retest identity and 50/180 pressure are permitted')
    root = PurePosixPath(value['root'])
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('Absolute owned retest root required')
    return value


def runtime_identity():
    path = os.getenv(MANIFEST_ENV)
    if path:
        value = validate_manifest(json.loads(Path(path).read_text(encoding='utf-8')))
        return Path(value['root']), value['project'], value['port']
    return Path('/home/ktdata/student-capacity-tests/20261016'), 'kt-school-e2e-capacity-20261016', 18084


def phase_bounds(workload, *, workload_profile=WORKLOAD_PROFILE):
    if workload not in ('comparison-2q', 'paper-20q'):
        raise ValueError('Unknown retest workload')
    result = {'preparation_seconds': 300, 'answer_seconds': 600 if workload == 'paper-20q' else 30,
              'acceptance_seconds': 60, 'processing_seconds': 300, 'statistics_seconds': 300}
    if workload_profile == WORKLOAD_PROFILE:
        result['login_window_seconds'] = 10
    elif workload_profile != HISTORICAL_PROFILE:
        raise ValueError('Unknown workload profile')
    return result | {'observer_seconds': sum(result.values()) + 60}


def retest_cells(stage, candidates, modes=MODES, *, counts=(50, 180), rounds=1):
    if (not set(counts) <= {50, 180} or not counts or not set(modes) <= set(MODES)
            or rounds < 1 or not candidates):
        raise ValueError('Retest pressure requires only 50/180 and explicit known modes')
    return [{'stage': stage, 'candidate': candidate, 'count': count, 'mode': mode, 'round': repeat,
             'transport_protocol': 'capacity-v3', 'workload': 'paper-20q', 'workload_profile': WORKLOAD_PROFILE,
             'browser_count': 2 if stage == 'final' else 0}
            for repeat in range(1, rounds + 1) for count in counts for mode in modes for candidate in candidates]


def role_counts(mode, count):
    if mode not in LEGACY_MODES or count not in (50, 180):
        raise ValueError('Unknown mode or pressure denominator')
    active = count // 3
    if mode == 'entry-mixed':
        first = (count - active) // 2
        return {'login': first, 'startup': count - active - first, 'save': active}
    if mode.endswith('-mixed'):
        return {mode.split('-')[0]: count - active, 'save': active}
    return {'save' if mode == 'save-only' else mode: count}


def connection_budget(values):
    pools = sum(int(values['ASSESSMENT_' + key + '_DB_CONNECTIONS']) for key in POOL_KEYS)
    reserve = sum(int(values.get('ASSESSMENT_' + key + '_DB_RESERVE_CONNECTIONS', 0)) for key in POOL_KEYS)
    extras = sum(int(values[key]) for key in ('SCHOOL_DB_DIRECT_CONNECTIONS', 'SCHOOL_DB_MONITOR_CONNECTIONS',
                                             'SCHOOL_DB_MAINTENANCE_CONNECTIONS', 'SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS'))
    return {'ordinary': pools, 'reserves': reserve, 'extra': extras, 'planned': pools + reserve + extras,
            'available': int(values['SCHOOL_DB_MAX_CONNECTIONS']) - int(values['SCHOOL_DB_RESERVED_CONNECTIONS'])}


def grow_candidate(parameters, dimension):
    result = copy.deepcopy(parameters)
    def put(key, value):
        result[key] = str(value)
    def grow(key):
        put(key, int(result[key]) + max(1, math.ceil(int(result[key]) * .25)))
    dimensions = {'assessment-workers': 'ASSESSMENT_REMAINING_WORKERS',
                  'capture-workers': 'ASSESSMENT_CAPTURE_WORKERS', 'entry-workers': 'ASSESSMENT_ENTRY_WORKERS',
                  'core-workers': 'ASSESSMENT_CORE_WORKERS', 'teacher-workers': 'ASSESSMENT_TEACHER_WORKERS',
                  'login-limit': 'ASSESSMENT_ENTRY_LOGIN_LIMIT', 'startup-limit': 'ASSESSMENT_ENTRY_STARTUP_LIMIT',
                  'heavy-limit': 'ASSESSMENT_ENTRY_HEAVY_LIMIT'}
    if dimension in dimensions:
        grow(dimensions[dimension])
    elif dimension.endswith('-pool') and dimension[:-5] in ('assessment', 'entry', 'capture', 'core', 'teacher'):
        name = 'REMAINING' if dimension[:-5] == 'assessment' else dimension[:-5].upper()
        pool, worker = 'ASSESSMENT_' + name + '_DB_CONNECTIONS', 'ASSESSMENT_' + name + '_WORKERS'
        put(pool, max(int(result[worker]), int(result[pool]) + max(1, math.ceil(int(result[pool]) * .25))))
    elif dimension == 'submission':
        grow('ASSESSMENT_SUBMISSION_WORKERS')
        put('ASSESSMENT_SUBMISSION_DB_CONNECTIONS', int(result['ASSESSMENT_SUBMISSION_WORKERS']))
    elif dimension == 'statistics':
        grow('ASSESSMENT_ANALYTICS_WORKERS')
        put('ASSESSMENT_BACKGROUND_DB_CONNECTIONS', 2 * int(result['ASSESSMENT_ANALYTICS_WORKERS']))
    elif dimension == 'burst':
        put('ASSESSMENT_ENTRY_STARTUP_BURST', 2 * int(result['ASSESSMENT_ENTRY_STARTUP_BURST']))
    elif dimension == 'rate':
        grow('ASSESSMENT_ENTRY_STARTUP_RATE')
        put('ASSESSMENT_ADMISSION_RATE_PER_SECOND', result['ASSESSMENT_ENTRY_STARTUP_RATE'])
    elif dimension == 'login-reduce':
        put('ASSESSMENT_ENTRY_LOGIN_LIMIT', max(1, math.floor(int(result['ASSESSMENT_ENTRY_LOGIN_LIMIT']) * .8)))
    else:
        raise ValueError('Unknown evidence-driven configuration dimension')
    if dimension == 'startup-limit':
        put('ASSESSMENT_ADMISSION_CAPACITY', result['ASSESSMENT_ENTRY_STARTUP_LIMIT'])
        put('ASSESSMENT_ENTRY_DB_CONNECTIONS', max(int(result['ASSESSMENT_ENTRY_DB_CONNECTIONS']),
                                                  int(result['ASSESSMENT_ADMISSION_CAPACITY'])))
    put('ASSESSMENT_ENTRY_HEAVY_LIMIT', max(int(result['ASSESSMENT_ENTRY_HEAVY_LIMIT']),
        int(result['ASSESSMENT_ENTRY_LOGIN_LIMIT']), int(result['ASSESSMENT_ENTRY_STARTUP_LIMIT'])))
    put('ASSESSMENT_ENTRY_WORKERS', max(int(result['ASSESSMENT_ENTRY_WORKERS']),
                                       int(result['ASSESSMENT_ENTRY_HEAVY_LIMIT']) + 1))
    budget = connection_budget(result)
    required = budget['planned'] + int(result['SCHOOL_DB_RESERVED_CONNECTIONS'])
    put('SCHOOL_DB_MAX_CONNECTIONS', max(int(result['SCHOOL_DB_MAX_CONNECTIONS']), math.ceil(required / 8) * 8))
    clients = sum(int(result[key]) for key in HTTP_WORKERS.values()) + int(result['ASSESSMENT_SUBMISSION_WORKERS'])
    clients += 2 * int(result['ASSESSMENT_ANALYTICS_WORKERS']) + int(result['SCHOOL_DB_MONITOR_CONNECTIONS'])
    put('PGBOUNCER_MAX_CLIENT_CONN', max(200, 2 * clients))
    return result


def formal_passed(rows):
    required = {(count, mode, repeat) for count in (50, 180) for mode in MODES for repeat in (1, 2)}
    relevant = [row for row in rows if row.get('stage') == 'final']
    observed = [(row.get('count'), row.get('mode'), row.get('round')) for row in relevant]
    identities = {json.dumps(row.get('evidence_identity'), sort_keys=True) for row in relevant}
    return (len(observed) == len(required) and set(observed) == required and len(identities) == 1
            and all(row.get('workload_profile') == WORKLOAD_PROFILE
                    and (row.get('evidence_identity') or {}).get('workload_profile') == WORKLOAD_PROFILE for row in relevant)
            and all(row.get('passed') is True and row.get('browser_passed') is True
                    and row.get('generator_valid') is True and row.get('monitor_ok') is True for row in relevant))
