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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'load'))
from school_diagnostic_runtime import (BASE_URL, DATABASE, PROJECT, ROLES, DiagnosticRuntime,
                                       SafetyInterrupted, read, read_evidence, write_once)
from school_architecture_load import validate_profile


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
    return None


class ArchitectureRuntime(DiagnosticRuntime):
    @property
    def observer_script(self):
        return self.candidate.parent / 'server/scripts/load/observe_school_runtime.py'

    def load_command(self, attempt, fixture_id, args):
        command = super().load_command(attempt, fixture_id, args)
        command[command.index('--memory') + 1] = '1g'
        return command[:2] + ['--cpus', '2'] + command[2:]

    def seed(self, stage, attempt):
        mode, count = stage_profile(stage)
        self.verify_runtime()
        self.bind_attempt(attempt, create=True)
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
        self.verify_runtime()
        self.bind_attempt(attempt)
        fixture = read(attempt / 'fixture.json')
        write_once(attempt / 'started.json', {'started_at': time.time(), 'profile': profile})
        write_once(attempt / 'backlog-before.json', {'global': self.backlog(), 'stage': self.backlog(fixture['task_id'])})
        stop = attempt / 'observer.stop'
        log = (attempt / 'observer.log').open('x', encoding='utf-8')
        os.chmod(attempt / 'observer.log', 0o600)
        self.observer = subprocess.Popen([sys.executable, str(self.observer_script), '--project', PROJECT,
            '--database-container', DATABASE, '--scope', 'short' if profile['mode'] == 'diagnostic' else 'formal',
            '--duration', str(profile['hard_timeout_seconds'] + 360), '--stop-file', str(stop),
            '--output', str(attempt / 'observation.json')], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
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
        self.run_private(self.compose + ['logs', '--no-color', '--since', str(int(read(attempt / 'started.json')['started_at'])), *ROLES],
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
                  'browser_acceptance': 'pending_separate_three_browser_evidence', 'formal_capacity_accepted': False,
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
        for child in runtime.children:
            runtime.terminate_child(child)


if __name__ == '__main__':
    main()
