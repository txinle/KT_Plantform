#!/usr/bin/env python3
"""Bounded read-only Redis witnesses; sampling cannot establish a capacity verdict."""
import argparse
import hashlib
import json
import math
import time
from pathlib import Path


TIMESTAMPS = ('requested_at', 'admitted_at', 'execution_started_at', 'released_at')
FIELDS = ('user_id', 'task_id', 'action', 'state', 'execution_id', 'lease_until') + TIMESTAMPS
SNAPSHOT_SCRIPT = """
local clock = redis.call('TIME')
local prefix = KEYS[1]
local rows = {}
for n = 1, #ARGV, 2 do
  local token = redis.call('GET', prefix .. ':request:' .. ARGV[n] .. ':' .. ARGV[n+1] .. ':start')
  local row = {}
  if token then
    row.token = token
    row.values = redis.call('HMGET', prefix .. ':token:' .. token,
      'user_id', 'task_id', 'action', 'state', 'execution_id', 'lease_until',
      'requested_at', 'admitted_at', 'execution_started_at', 'released_at')
  end
  table.insert(rows, row)
end
return cjson.encode({server_time=tonumber(clock[1])+tonumber(clock[2])/1000000,
  admitted=redis.call('ZCARD', prefix .. ':leases'),
  executing=redis.call('ZCARD', prefix .. ':executing'), rows=rows})
"""


def fixture_bindings(fixture):
    accounts = fixture.get('accounts', [])
    capacity = fixture.get('settings_binding', {}).get('ASSESSMENT_ADMISSION_CAPACITY')
    if fixture.get('isolated_demo') is not True or len(accounts) != 180:
        raise ValueError('Requires one isolated frozen 180-account fixture')
    if type(capacity) is not int or capacity < 1:
        raise ValueError('Fixture admission capacity missing')
    bindings = []
    for account in accounts:
        values = (account['user_id'], account['taskId'], account['session_id'])
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError('Fixture requires positive integer user/task/session references')
        bindings.append(dict(zip(('user_id', 'task_id', 'session_id'), values)))
    if len({(row['task_id'], row['user_id']) for row in bindings}) != len(bindings):
        raise ValueError('Fixture contains duplicate request identities')
    return bindings, capacity


def read_sample(client, prefix, accounts):
    arguments = [str(row[key]) for row in accounts for key in ('task_id', 'user_id')]
    result = json.loads(client.eval_ro(SNAPSHOT_SCRIPT, 1, prefix, *arguments))
    if len(result['rows']) != len(accounts):
        raise ValueError('Incomplete atomic snapshot')
    rows = []
    for binding, raw in zip(accounts, result.pop('rows')):
        row = dict(binding)
        if not raw or not raw.get('token'):
            row.update(measurement='missing', reason='request_binding_absent')
        else:
            values = dict(zip(FIELDS, raw['values']))
            if (values.get('user_id'), values.get('task_id'), values.get('action')) != (
                    str(binding['user_id']), str(binding['task_id']), 'start'):
                row.update(measurement='missing', reason='token_identity_missing_or_mismatched')
            else:
                row.update(measurement='sampled', token_sha256=hashlib.sha256(raw['token'].encode()).hexdigest(),
                           state=values['state'])
                # Only stored server timestamps are evidence of lifecycle transitions.
                for name in ('lease_until',) + TIMESTAMPS:
                    if values.get(name) not in (None, False, ''):
                        number = float(values[name])
                        if not math.isfinite(number):
                            raise ValueError('Nonfinite Redis timestamp')
                        row[name] = number
                row['missing_timestamp_fields'] = [name for name in TIMESTAMPS if name not in row]
        rows.append(row)
    result.update(measurement='sampled', occupied=result['admitted'] + result['executing'], accounts=rows)
    return result


class Summary:
    def __init__(self, capacity, fixture_sha256):
        self.capacity, self.fixture_sha256 = capacity, fixture_sha256
        self.sampled = self.missing = 0
        self.peak = None
        self.first = self.last = None
        self.accounts = {}

    def add(self, sample):
        if sample['measurement'] != 'sampled':
            self.missing += 1
            return
        self.sampled += 1
        self.peak = max(self.peak or 0, sample['occupied'])
        self.first = sample['server_time'] if self.first is None else self.first
        self.last = sample['server_time']
        for row in sample['accounts']:
            key = (row['task_id'], row['user_id'])
            previous = self.accounts.get(key, {})
            current = dict(row)
            if not row.get('token_sha256') or row.get('token_sha256') == previous.get('token_sha256'):
                current = previous | row
            if row.get('state') in ('succeeded', 'failed'):
                current['released_state_observed'] = row['state']
            for label, start, end in (
                    ('reservation_seconds', 'admitted_at', 'execution_started_at'),
                    ('execution_seconds', 'execution_started_at', 'released_at')):
                current[label] = (current[end] - current[start]
                                  if start in current and end in current and current[end] >= current[start] else None)
            current['duration_measurement'] = ('sampled' if current['reservation_seconds'] is not None
                                               and current['execution_seconds'] is not None else 'missing')
            self.accounts[key] = current

    def result(self):
        return {'measurement': ('missing' if not self.sampled else 'partial' if self.missing else 'sampled'),
                'fixture_sha256': self.fixture_sha256, 'global_c_from_fixture': self.capacity,
                'global_c_source': 'frozen_fixture.settings_binding; Redis does not store configured C',
                'successful_samples': self.sampled, 'missing_samples': self.missing,
                'peak_occupied_observed': self.peak,
                'peak_occupied_over_fixture_c_observed': self.peak / self.capacity if self.peak is not None else None,
                'first_server_time': self.first, 'last_server_time': self.last,
                'accounts': list(self.accounts.values()),
                'limitations': ['Sampled occupancy includes expired reservations pending coordinator cleanup.',
                                'Transitions between samples may be missed; peak is an observed lower bound.',
                                'No timestamp or duration is inferred from lease deadlines or sample times.',
                                'Per-account summary retains latest observed token; JSONL retains all samples.',
                                'No capacity conclusion is made.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('redis-url', 'prefix', 'fixture', 'output', 'summary', 'stop-file'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--duration', type=float, required=True)
    parser.add_argument('--interval-ms', type=int, default=100)
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or not 0 < args.duration <= 7200 or not 50 <= args.interval_ms <= 60000:
        parser.error('duration must be 0..7200 seconds and interval-ms 50..60000')
    if not args.prefix or len(args.prefix) > 200 or any(char.isspace() for char in args.prefix):
        parser.error('prefix must be nonempty, bounded, and contain no whitespace')
    fixture_bytes = Path(args.fixture).read_bytes()
    accounts, capacity = fixture_bindings(json.loads(fixture_bytes))
    summary = Summary(capacity, hashlib.sha256(fixture_bytes).hexdigest())
    import redis
    client = redis.Redis.from_url(args.redis_url, decode_responses=True,
                                 socket_connect_timeout=1, socket_timeout=1)
    until = time.monotonic() + args.duration
    stop_reason = 'duration'
    # Exclusive creation prevents accidentally overwriting a frozen evidence file.
    with Path(args.output).open('x', encoding='utf-8') as output, Path(args.summary).open('x', encoding='utf-8') as final:
        try:
            while time.monotonic() < until:
                if Path(args.stop_file).exists():
                    stop_reason = 'stop_file'
                    break
                sampled_at = time.monotonic()
                try:
                    sample = read_sample(client, args.prefix, accounts)
                except (redis.RedisError, ValueError, KeyError, TypeError) as exc:
                    # Exception text may contain connection credentials or token values.
                    sample = {'measurement': 'missing', 'error_type': type(exc).__name__}
                sample['observer_wall_time'] = time.time()
                output.write(json.dumps(sample, ensure_ascii=True) + '\n')
                output.flush()
                summary.add(sample)
                time.sleep(max(0, min(until - time.monotonic(),
                                     args.interval_ms / 1000 - (time.monotonic() - sampled_at))))
        except KeyboardInterrupt:
            stop_reason = 'interrupted'
        finally:
            final.write(json.dumps(summary.result() | {'stop_reason': stop_reason}, ensure_ascii=True, indent=2) + '\n')
            client.close()
    return 0 if summary.sampled and not summary.missing else 2


if __name__ == '__main__':
    raise SystemExit(main())
