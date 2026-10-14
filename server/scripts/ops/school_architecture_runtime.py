#!/usr/bin/env python3
"""Isolated, bounded school acceptance coordinator for the server/client build."""
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'load'))
from school_diagnostic_runtime import (BASE_URL, DATABASE, PROJECT, ROLES, DiagnosticRuntime,
                                       SafetyInterrupted, read, read_evidence, write_once)
from school_architecture_load import validate_profile


GATEWAY_ROUTES = (
    ('web', '/api/v1/auth/csrf/', (200,)),
    ('student-read', '/api/v1/student/courses/1/learning/', (401, 403)),
    ('teacher-read', '/api/v1/teacher/courses/1/data/overview/', (401, 403)),
)


class NoGatewayRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def stage_profile(stage):
    if re.fullmatch(r'diagnostic-(1|3|10|50|180)', stage):
        return 'diagnostic', int(stage.split('-')[1])
    if re.fullmatch(r'smoke-(3|10)', stage):
        return 'smoke', int(stage.split('-')[1])
    if re.fullmatch(r'formal-[12]', stage):
        return 'formal', 180
    if stage == 'browser-3':
        return 'smoke', 3
    raise ValueError('Stage must be one of the approved diagnostic, smoke, formal or browser fixtures')


def confirmed_fact_failure(persistence):
    if persistence.get('raw_fact_integrity_failed') is True:
        return 'raw_fact_integrity_failed'
    checks = persistence.get('known_fact_integrity_checks', {})
    for key in (
        'all_confirmed_entries_match_original_material',
        'all_acknowledged_events_persisted_and_material_matched',
        'all_acknowledged_checkpoints_persisted_and_original_fields_matched',
        'present_unacknowledged_events_material_matched',
        'present_unacknowledged_checkpoints_original_fields_matched',
        'zero_unexpected_or_duplicate_client_events',
        'zero_unexpected_or_duplicate_checkpoints',
        'unique_known_client_sequences_and_ingest_ordinals',
    ):
        if checks.get(key) is False:
            return key
    if persistence.get('known_navigation_integrity_passed') is False:
        return 'known_navigation_integrity_passed'
    if persistence.get('known_capture_mutation_integrity_passed') is False:
        return 'known_capture_mutation_integrity_passed'
    return None


def acceptance_fields(scope, stage, checks):
    if scope not in ('protocol_only', 'protocol_and_browser'):
        raise ValueError('Explicit supported acceptance scope required')
    required = ('load_completed', 'operation_targets_passed', 'data_integrity_passed',
                'grading_coverage_export_consistent', 'background_drained', 'observer_completed',
                'no_oom', 'observer_covers_load')
    protocol_passed = stage in ('formal-1', 'formal-2') and all(checks.get(key) is True for key in required)
    return {'acceptance_scope': scope, 'formal_protocol_capacity_accepted': protocol_passed,
            'browser_acceptance': 'not_requested_by_user' if scope == 'protocol_only' else 'pending_separate_three_browser_evidence',
            'formal_capacity_accepted': False}


class ArchitectureRuntime(DiagnosticRuntime):
    @property
    def gateway_routes(self):
        if self.six_pool_profile:
            return (
                ('auth', 'GET', '/api/v1/auth/csrf/', (200,)),
                ('assessment', 'GET', '/api/v1/student/tasks/1/recovery/', (401, 403)),
                ('capture', 'POST', '/api/v1/assessment/process/capture-v2/batch/', (401, 403)),
                ('student-read', 'GET', '/api/v1/student/courses/1/learning/', (401, 403)),
                ('teacher-read', 'GET', '/api/v1/teacher/courses/1/data/overview/', (401, 403)),
            )
        return tuple((role, 'GET', path, statuses) for role, path, statuses in GATEWAY_ROUTES)

    @property
    def observer_script(self):
        return self.candidate.parent / 'server/scripts/load/observe_school_runtime.py'

    def load_command(self, attempt, fixture_id, args):
        command = super().load_command(attempt, fixture_id, args)
        command[command.index('--memory') + 1] = '1g'
        return command[:2] + ['--cpus', '2'] + command[2:]

    def verify_gateway_routing(self, attempt):
        deadline = time.monotonic() + 18
        since = str(int(time.time()) - 1)
        routes = self.gateway_routes
        requests = [{'request_id': uuid.uuid4().hex, 'expected_role': role, 'observed_roles': []}
                    for role, _, _, _ in routes]
        result = {'passed': False, 'requests': requests}

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError()
            return min(3, seconds)

        def docker(*args):
            process = subprocess.run(['docker', *args], text=True, capture_output=True,
                                     timeout=remaining(), check=False)
            if process.returncode:
                raise OSError()
            return process

        try:
            identities = docker('ps', '-q', '--no-trunc', '--filter',
                                'label=com.docker.compose.project=' + PROJECT).stdout.split()
            if not identities:
                raise ValueError()
            roles = {}
            for item in json.loads(docker('inspect', *identities).stdout):
                labels = item['Config'].get('Labels', {})
                role = labels.get('com.docker.compose.service')
                if role not in {route[0] for route in routes} or labels.get('com.docker.compose.oneoff', '').lower() == 'true':
                    continue
                if (labels.get('com.docker.compose.project') != PROJECT or role in roles
                        or not item['State']['Running'] or item['Image'] != self.config['images'][role]):
                    raise ValueError()
                roles[role] = item['Id']
            if set(roles) != {route[0] for route in routes}:
                raise ValueError()
            opener = urllib.request.build_opener(NoGatewayRedirect())
            statuses_valid = True
            for row, (_, method, path, statuses) in zip(requests, routes):
                request = urllib.request.Request(BASE_URL + path, method=method,
                    data=b'{}' if method == 'POST' else None,
                    headers={'X-Request-ID': row['request_id'], 'Content-Type': 'application/json'})
                try:
                    with opener.open(request, timeout=remaining()) as response:
                        statuses_valid &= response.status in statuses
                except urllib.error.HTTPError as error:
                    statuses_valid &= error.code in statuses
                    error.close()
                except (OSError, TimeoutError):
                    statuses_valid = False
            by_identity = {row['request_id']: row for row in requests}
            for role, identity in roles.items():
                process = docker('logs', '--since', since, '--tail', '2000', identity)
                for line in (process.stdout + '\n' + process.stderr).splitlines():
                    beginning = line.find('{')
                    if beginning < 0:
                        continue
                    try:
                        timing = json.loads(line[beginning:])
                    except ValueError:
                        continue
                    if not isinstance(timing, dict) or timing.get('phase') != 'gunicorn_request_complete':
                        continue
                    row = by_identity.get(timing.get('request_id'))
                    if row is not None and role not in row['observed_roles']:
                        row['observed_roles'].append(role)
            result['passed'] = (statuses_valid and time.monotonic() <= deadline
                                and all(row['observed_roles'] == [row['expected_role']] for row in requests))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass
        write_once(attempt / 'gateway-routing.json', result)
        if not result['passed']:
            raise SafetyInterrupted('Gateway routing is mismatched or unwitnessed')
        return result

    def seed(self, stage, attempt):
        mode, count = stage_profile(stage)
        self.verify_runtime()
        self.bind_attempt(attempt, create=True)
        self.verify_gateway_routing(attempt)
        run = 'arch-' + hashlib.sha256((self.config['run_id'] + ':' + stage).encode()).hexdigest()[:18]
        script = '''import io,json,os,secrets
from pathlib import Path
from django.core.management import call_command
from django.db import connection
from apps.experiments.models import ClassExperimentTask
assert connection.settings_dict['NAME']=='kt_e2e'
captured=io.StringIO()
call_command('seed_assessment_load',run_id=RUN,students=COUNT,items=20,password=secrets.token_urlsafe(24),capture_protocol=True,feedback_mode='full',stdout=captured)
fixture=json.loads(captured.getvalue())
task=ClassExperimentTask.objects.select_related('experiment').get(pk=fixture['task_id'])
fixture.update(database='kt_e2e',course_id=task.experiment.course_id,experiment_id=task.experiment_id,class_id=task.class_group_id,source_config=json.loads(Path('/attempt/source-config.json').read_text()))
with Path('/attempt/fixture.json').open('x') as target:
 json.dump(fixture,target)
os.chmod('/attempt/fixture.json',0o600)
'''.replace('RUN', repr(run)).replace('COUNT', str(count))
        code = self.run_private(self.direct_command(attempt, ['manage.py', 'shell', '-c', script]), attempt, 'seed', 600)
        if code:
            raise RuntimeError('Isolated fixture preparation failed; see private seed log')
        return validate_profile(read(attempt / 'fixture.json'), mode)

    def drain(self, attempt, task_id):
        deadline = time.monotonic() + 300
        rows = []
        while True:
            sample = self.safety_sample(attempt)
            reason = self.guard.update(sample)
            if reason:
                raise SafetyInterrupted(reason)
            counts = self.backlog(task_id)
            rows.append({'at': time.time(), 'counts': counts})
            if not any(counts.values()):
                passed = True
                break
            if time.monotonic() >= deadline:
                passed = False
                break
            time.sleep(2)
        result = {'passed': passed, 'budget_seconds': 300, 'samples': rows}
        write_once(attempt / 'drain.json', result)
        return result

    def run_stage(self, stage, attempt, profile):
        scope = self.config.get('acceptance_scope', 'protocol_and_browser')
        acceptance_fields(scope, stage, {})
        self.verify_runtime()
        self.bind_attempt(attempt)
        gateway, gateway_error = read_evidence(attempt / 'gateway-routing.json')
        witnessed = gateway.get('requests', [])
        if (gateway_error or gateway.get('passed') is not True or len(witnessed) != len(self.gateway_routes)
                or [row.get('expected_role') for row in witnessed] != [route[0] for route in self.gateway_routes]
                or any(row.get('observed_roles') != [row.get('expected_role')] for row in witnessed)
                or len({row.get('request_id') for row in witnessed if re.fullmatch('[a-f0-9]{32}', row.get('request_id', ''))}) != len(self.gateway_routes)):
            raise SafetyInterrupted('Gateway routing evidence is absent or failed before load')
        fixture = read(attempt / 'fixture.json')
        write_once(attempt / 'started.json', {'started_at': time.time(), 'profile': profile})
        write_once(attempt / 'backlog-before.json', {'global': self.backlog(), 'stage': self.backlog(fixture['task_id'])})
        stop = attempt / 'observer.stop'
        log = (attempt / 'observer.log').open('x', encoding='utf-8')
        os.chmod(attempt / 'observer.log', 0o600)
        self.observer = subprocess.Popen([sys.executable, str(self.observer_script), '--project', PROJECT,
            '--database-container', self.database_container, '--scope', 'short' if profile['mode'] == 'diagnostic' else 'formal',
            '--duration', str(profile['hard_timeout_seconds'] + 360), '--stop-file', str(stop),
            '--output', str(attempt / 'observation.json'),
            *(['--database-probe-progress', str(attempt / 'database-probe.progress.json')]
              if self.native_probe is not None else [])], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        self.children.append(self.observer)
        try:
            time.sleep(2)
            if self.observer.poll() is not None:
                raise RuntimeError('Observer failed before load; see private observer log')
            argv = ['scripts/load/school_architecture_load.py', '--manifest', '/attempt/fixture.json',
                    '--output', '/attempt/http.json', '--mode', profile['mode'], '--base-url', BASE_URL]
            code = self.monitored_process(self.load_command(attempt, stage, argv), attempt, 'load', profile['hard_timeout_seconds'])
            drain = self.drain(attempt, fixture['task_id'])
        finally:
            stop.touch(mode=0o600, exist_ok=True)
            try:
                self.observer.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.terminate_child(self.observer)
        self.run_private(self.compose + ['logs', '--no-color', '--since', str(int(read(attempt / 'started.json')['started_at'])), *self.runtime_roles],
                         attempt, 'services', 60)
        verify_code = self.run_private(self.direct_command(attempt, ['manage.py', 'verify_school_protocol_load',
            '--manifest', '/attempt/fixture.json', '--output', '/attempt/persistence.json',
            '--workload-version', 'browser-aligned-v2']), attempt, 'persistence', 240)
        persistence, persistence_error = read_evidence(attempt / 'persistence.json')
        fact_failure = confirmed_fact_failure(persistence)
        if fact_failure:
            raise SafetyInterrupted('Persisted fact integrity failed: ' + fact_failure)
        results_code = self.run_private(self.direct_command(attempt, ['manage.py', 'verify_school_architecture_results',
            '--manifest', '/attempt/fixture.json', '--output', '/attempt/architecture-results.json']),
            attempt, 'architecture-results', 240)
        http, http_error = read_evidence(attempt / 'http.json')
        results, results_error = read_evidence(attempt / 'architecture-results.json')
        fact_failure = confirmed_fact_failure(results)
        if fact_failure:
            raise SafetyInterrupted('Persisted fact integrity failed: ' + fact_failure)
        observation, observer_error = read_evidence(attempt / 'observation.json')
        checks = {'load_completed': code == 0 and http.get('workflow_completed') is True,
            'operation_targets_passed': http.get('performance_passed') is True,
            'data_integrity_passed': verify_code == 0 and persistence.get('passed') is True,
            'grading_coverage_export_consistent': results_code == 0 and results.get('passed') is True,
            'background_drained': drain['passed'],
            'observer_completed': self.observer.returncode == 0 and observation.get('state') == 'completed',
            'no_oom': observation.get('oom_kills') == 0,
            'observer_covers_load': (bool(http) and observation.get('started_at', float('inf')) <= http.get('started', 0)
                and observation.get('ended_at', 0) >= http.get('ended', float('inf')))}
        result = {'stage': stage, 'profile': profile, 'checks': checks, 'protocol_passed': all(checks.values()),
                  **acceptance_fields(scope, stage, checks),
                  'process_exit_code': code, 'source_config': self.source_value(),
                  'evidence_errors': {key: error for key, error in (('http', http_error), ('persistence', persistence_error),
                      ('architecture_results', results_error), ('observer', observer_error)) if error}}
        write_once(attempt / 'result.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    stage_profile(args.stage)
    if os.geteuid() != 0:
        parser.error('Use authenticated sudo on the school host')
    runtime = ArchitectureRuntime(read(args.config))
    attempt = runtime.private / args.stage
    attempt.mkdir(mode=0o700)
    def interrupted(signum, frame):
        raise SafetyInterrupted('Coordinator interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        profile = runtime.seed(args.stage, attempt)
        if args.prepare_only or args.stage == 'browser-3':
            print(json.dumps({'stage': args.stage, 'fixture_prepared': True, 'load_executed': False}))
            return
        result = runtime.run_stage(args.stage, attempt, profile)
        print(json.dumps(result))
        raise SystemExit(0 if result['protocol_passed'] else 1)
    except SafetyInterrupted as error:
        runtime.stop_load()
        runtime.stop_owned()
        write_once(attempt / 'safety-interrupted.json', {'reason': str(error), 'at': time.time()})
        raise SystemExit(75)
    finally:
        runtime.stop_load()
        if runtime.native_probe is not None:
            runtime.native_probe.close()
        for child in runtime.children:
            runtime.terminate_child(child)


if __name__ == '__main__':
    main()
