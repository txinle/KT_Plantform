"""Frozen policy and paired selection for the 8/12 worker short diagnostic."""
import math

VERSION = 'worker-short-20261014-v1'
POLICY = {'version': VERSION, 'workers': 8, 'assessment_pool_size': 8,
          'duration_seconds': 360, 'entry_deadline_seconds': 120,
          'observer_interval_seconds': 2, 'minimum_counts_by_item': {'0': 150, '1': 120}}


def validate_policy(value):
    if not isinstance(value, dict) or set(value) != set(POLICY):
        raise ValueError('Exact frozen worker short policy required')
    if value.get('workers') not in (8, 12) or type(value.get('workers')) is not int:
        raise ValueError('Only 8/12 assessment workers are authorized in this stage')
    if value.get('assessment_pool_size') not in (8, 12):
        raise ValueError('Only 8+2 or conditionally authorized 12+2 connections allowed')
    if value.get('assessment_pool_size') == 12 and value['workers'] != 12:
        raise ValueError('Expanded pool is restricted to the 12 worker candidate')
    if any(value[key] != default for key, default in POLICY.items()
           if key not in ('workers', 'assessment_pool_size')):
        raise ValueError('Short measurement, entry and sampling budgets are frozen')
    return value


def armed_matches(protocol, browser, binding, protocol_count):
    return (protocol.get('protocol_count') == protocol_count and browser.get('probe_count') == 2
            and all(type(row.get('pid')) is int and row['pid'] > 0 for row in (protocol, browser))
            and all(row.get(key) == binding.get(key) for row in (protocol, browser)
                    for key in ('fixture_sha256', 'plan_sha256'))
            and browser.get('fixture_binding') == binding)


def short_stop_reason(policy, start_at, tick, protocol_ready, browser_ready, entered):
    if tick >= start_at + policy['entry_deadline_seconds'] and not entered:
        return 'short_all_180_entry_deadline'
    if tick >= start_at + policy['duration_seconds']:
        return 'short_fixed_window_complete'
    return None


def protocol_finished_error(completed, count):
    return bool(completed.get('errors')) or (count > 0 and completed.get('workflow_completed') is not True)


def quantile(values, q):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * q) - 1)] if values else None


def compare_runs(before, after):
    groups = []
    for item in (0, 1):
        selections = [[row for row in run.get('operations', [])
                       if row.get('metric') == 'submit_next_ready' and row.get('item_index') == item]
                      for run in (before, after)]
        keyed = [{row['student_index']: row for row in rows} for rows in selections]
        valid = (set(keyed[0]) == set(keyed[1]) and all(len(keys) == len(rows)
                 for keys, rows in zip(keyed, selections))
                 and all(row.get('successful') is True for rows in selections for row in rows))
        enough = len(keyed[0]) >= POLICY['minimum_counts_by_item'][str(item)]
        baseline, candidate = ([row['elapsed_seconds'] for row in rows] for rows in selections)
        p95_a, p95_b = quantile(baseline, .95), quantile(candidate, .95)
        median_a, median_b = quantile(baseline, .5), quantile(candidate, .5)
        improved = bool(valid and enough and p95_a and p95_b is not None
                        and p95_b <= p95_a * .8 and median_b <= median_a)
        groups.append({'item_index': item, 'baseline_count': len(baseline), 'candidate_count': len(candidate),
                       'same_students': valid, 'enough_samples': enough,
                       'baseline_p95': p95_a, 'candidate_p95': p95_b,
                       'baseline_median': median_a, 'candidate_median': median_b,
                       'improved': improved})
    secondary = all(isinstance(before.get('secondary_p95', {}).get(key), (int, float))
                    and isinstance(after.get('secondary_p95', {}).get(key), (int, float))
                    and after['secondary_p95'][key] <= before['secondary_p95'][key] * 1.1
                    for key in ('login', 'save', 'events'))
    checks = {'both_eligible': before.get('eligible') is True and after.get('eligible') is True,
              'both_items_improved': all(row['improved'] for row in groups),
              'throughput_not_reduced': after.get('confirmed_submissions', -1) >= before.get('confirmed_submissions', 0),
              'secondary_not_regressed': secondary}
    return {'candidate_qualified': all(checks.values()), 'checks': checks, 'paired_items': groups,
            'formal_capacity_accepted': False}
