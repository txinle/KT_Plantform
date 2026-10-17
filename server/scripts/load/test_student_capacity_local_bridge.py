"""Local bridge ownership, frozen input and shared barrier contracts; no SSH."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import io

sys.path.insert(0, str(Path(__file__).parent))
import capacity_retest
import test_student_capacity_protocol_v3 as protocol_tests


class BridgeTests(unittest.TestCase):
    def test_idle_request_waits_and_all_completed_controller_states_exit(self):
        subject = self.subject()
        for state in ('completed', 'completed_without_qualified_configuration', 'completed_with_incomplete_final'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                manifest_path = Path(directory) / 'manifest.json'
                manifest_path.write_text(json.dumps(capacity_retest.default_manifest() | {'local_evidence_root': directory}))
                with patch.object(subject, 'Remote') as remote, patch.object(subject, 'ensure_forward', return_value=None), \
                        patch.object(subject, 'process_request') as process, patch.object(subject.time, 'sleep'), \
                        patch.object(sys, 'argv', ['bridge', '--run-manifest', str(manifest_path), '--checkout', directory]):
                    remote.return_value.read.side_effect = [b'{"state":"running"}', b'{"state":"idle"}',
                                                           json.dumps({'state': state}).encode()]
                    self.assertEqual(subject.main(), 0)
                    process.assert_not_called()

    def subject(self):
        self.assertIsNotNone(importlib.util.find_spec('run_student_capacity_local_bridge'), 'Local bridge is missing')
        import run_student_capacity_local_bridge
        return run_student_capacity_local_bridge

    def inputs(self):
        import student_capacity_protocol_v3 as protocol
        fixture = protocol_tests.fixture()
        raw = json.dumps(fixture).encode()
        cell = 'a' * 20
        plan = protocol.build_plan(fixture, mode='full-flow', count=50, run_id=cell,
            fixture_sha256=hashlib.sha256(raw).hexdigest(), base_url='http://127.0.0.1:18085',
            workload='paper-20q', browser_account_ids=['a0', 'a1'])
        root = '/home/ktdata/capacity-retest-bridge-20261016'
        request = {'version': 'student-capacity-driver-request-v1', 'cell_id': cell, 'run_id': cell,
            'mode': 'full-flow', 'count': 50, 'workload': 'paper-20q', 'workload_profile': capacity_retest.WORKLOAD_PROFILE,
            'actor_ids': [p['account_id'] for p in plan['roster']],
            'browser_account_ids': ['a0', 'a1'], 'fixture_sha256': hashlib.sha256(raw).hexdigest(), 'plan_id': plan['plan_id'],
            'bridge_directory': root + '/' + cell, 'fixture_file': root + '/' + cell + '/fixture.json',
            'plan_file': root + '/' + cell + '/plan.json', 'phase_bounds': capacity_retest.phase_bounds('paper-20q')}
        return request, raw, plan

    def test_owned_exact_request_and_full_split(self):
        subject = self.subject()
        request, _, _ = self.inputs()
        self.assertEqual(subject.validate_request(request, capacity_retest.default_manifest()), request)
        for change in ({'count': 48}, {'actor_ids': request['actor_ids'][:-1]},
                       {'workload_profile': 'historical-v3'}, {'mode': 'save-only'}, {'workload': 'comparison-2q'},
                       {'browser_account_ids': ['a0', 'a0']}, {'browser_account_ids': ['unknown', 'a1']},
                       {'bridge_directory': '/tmp/x'}, {'fixture_file': request['fixture_file'] + '/../other'},
                       {'cell_id': '../' + 'a' * 20}, {'phase_bounds': {'preparation_seconds': 99999}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                subject.validate_request(request | change, capacity_retest.default_manifest())

    def test_frozen_input_hashes_roster_and_plan_identity(self):
        subject = self.subject()
        request, raw, plan = self.inputs()
        subject.validate_inputs(request, raw, json.dumps(plan).encode())
        with self.assertRaises(ValueError):
            subject.validate_inputs(request, raw + b' ', json.dumps(plan).encode())
        plan['roster'][0]['driver'] = 'protocol'
        with self.assertRaises(ValueError):
            subject.validate_inputs(request, raw, json.dumps(plan).encode())

    def test_barrier_has_one_monotonic_and_wall_release_after_both_ready(self):
        subject = self.subject()
        request, _, _ = self.inputs()
        ready = {'ready': True, 'plan_id': request['plan_id'], 'run_id': request['run_id']}
        release = subject.release_document(request, [ready, ready], monotonic=100, wall=200)
        self.assertEqual(release, {'release_monotonic': 103, 'release_wall_time': 203})
        with self.assertRaises(ValueError):
            subject.release_document(request, [ready, ready | {'ready': False}], monotonic=100, wall=200)

    def test_driver_exit_one_is_evidence_not_a_capacity_pass_or_generator_failure(self):
        subject = self.subject()
        self.assertEqual(subject.driver_result({'protocol': 1, 'browser': 1}, artifacts_complete=True), 'driver_completed')
        self.assertEqual(subject.driver_result({'protocol': 2}, artifacts_complete=False), 'driver_failed')
        self.assertTrue(subject.classify_generator([{'available_memory_bytes': 2**30, 'heartbeat_lateness_ms': 1}],
            release_lateness_ms=0)['generator_valid'])
        self.assertFalse(subject.classify_generator([{'available_memory_bytes': 2**30, 'heartbeat_lateness_ms': 1500}],
            release_lateness_ms=0)['generator_valid'])

    def test_commands_use_checkout_python_exact_node_and_disjoint_ids(self):
        subject = self.subject()
        request, _, _ = self.inputs()
        commands = subject.driver_commands(request, Path('E:/checkout'), Path('E:/manifest.json'), Path('E:/cell'))
        self.assertTrue(str(commands['protocol'][0]).replace('\\', '/').endswith('/.venv/Scripts/python.exe'))
        self.assertIn('--browser-account-ids', commands['protocol'])
        self.assertIn('--actor-ids', commands['protocol'])
        self.assertIn('--workload-profile', commands['protocol'])
        self.assertEqual(str(commands['browser'][0]).replace('\\', '/'), subject.NODE.replace('\\', '/'))
        self.assertIn('--ready-file', commands['browser'])
        self.assertIn('--pressure-finished-file', commands['browser'])
        self.assertIn('--teacher-only', commands['visual'])

    def test_listener_probe_uses_the_existing_login_url_and_built_assets(self):
        subject = self.subject()
        paths = []
        def request(url, **kwargs):
            paths.append(url)
            if url.endswith('/login/'):
                return io.BytesIO(b'<script src="/static/spa-assets/assets/main.js"></script>')
            if url.endswith('/auth/csrf/'):
                return io.BytesIO(b'{"data":{"csrf_token":"fresh"}}')
            return io.BytesIO(b'asset')
        with patch.object(subject.urllib.request, 'urlopen', request):
            self.assertEqual(subject.target_fingerprint(), {'/static/spa-assets/assets/main.js': subject.sha(b'asset')})
        self.assertEqual(paths[0], subject.BASE_URL + '/login/')

    def test_failed_workloads_publish_raw_outputs_without_false_capacity_acceptance(self):
        subject = self.subject()
        request, fixture_raw, _ = self.inputs()
        request['browser_account_ids'] = []
        fixture = json.loads(fixture_raw)
        plan = subject.protocol.build_plan(fixture, mode=request['mode'], count=request['count'], run_id=request['run_id'],
            fixture_sha256=request['fixture_sha256'], base_url=subject.BASE_URL, workload=request['workload'],
            actor_ids=request['actor_ids'])
        request['plan_id'] = plan['plan_id']
        uploads = []
        class Remote:
            def cell_identity(_remote, _path):
                pass
            def download(_remote, path, local):
                raw = fixture_raw if path.endswith('fixture.json') else json.dumps(plan).encode()
                subject.private(local, raw)
                return raw
            def read(_remote, path, **kwargs):
                return b''
            def upload(_remote, path, relative, directory):
                uploads.append(relative)
            def call(_remote, _command):
                return b''
        class Monitor:
            rows = [{'available_memory_bytes': 2**30, 'heartbeat_lateness_ms': 0}]
            def __init__(self, _stream):
                pass
            def start(self):
                pass
            def stop(self):
                pass
        class Process:
            pid = 1
            returncode = None
            def __init__(self, command, **kwargs):
                self.command = command
                Path(command[command.index('--plan-output') + 1]).write_text(json.dumps(plan))
                Path(command[command.index('--ready-file') + 1]).write_text(json.dumps({
                    'ready': True, 'run_id': plan['run_id'], 'plan_id': plan['plan_id']}))
            def poll(self):
                release = Path(self.command[self.command.index('--release-file') + 1])
                if release.exists() and self.returncode is None:
                    records = [{**p, 'status': 'failed', 'plan_id': plan['plan_id'], 'run_id': plan['run_id'],
                                'release_skew_ms': 0} for p in plan['roster']]
                    Path(self.command[self.command.index('--output') + 1]).write_text('\n'.join(json.dumps(r) for r in records))
                    self.returncode = 1
                return self.returncode
        with tempfile.TemporaryDirectory() as directory:
            manifest = capacity_retest.default_manifest() | {'local_evidence_root': directory}
            with patch.object(subject.subprocess, 'Popen', Process), patch.object(subject, 'GeneratorMonitor', Monitor):
                done = subject.process_request(request, manifest, Path(directory), Path(directory) / 'manifest.json', Remote())
            self.assertEqual(done['state'], 'driver_completed')
            self.assertIsNone(done['capacity_verdict'])
            summary = json.loads((Path(directory) / 'cells' / request['cell_id'] / 'combined-summary.json').read_text())
            self.assertEqual(summary['counts']['failed'], 50)
            self.assertFalse(summary['thresholds_met'])
            self.assertIn('records.jsonl', done['files'])
            self.assertEqual(uploads[-1], 'done.json.pending')


if __name__ == '__main__':
    unittest.main()
