import importlib.util
import json
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path


LOAD = Path(__file__).resolve().parents[2] / 'scripts' / 'load'


def load_capture_protocol():
    sys.path.insert(0, str(LOAD))
    spec = importlib.util.spec_from_file_location('capture_protocol_durable_test', LOAD / 'capture_protocol.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidenceDurabilityTests(unittest.TestCase):
    def test_failed_progress_write_cannot_leave_a_passing_evidence_report(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            manifest, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            manifest.write_text(json.dumps({'task_id': 14, 'student_count': 2}))
            with module.evidence_files(manifest, output) as files:
                json.dump({'passed': True, 'workflow_completed': True, 'formal_capacity_accepted': True}, files['result'])
                files['result_ready'] = True
                files['progress_error_type'] = 'OSError'
            report = json.loads(output.read_text())
            self.assertFalse(report['passed'])
            self.assertFalse(report['formal_capacity_accepted'])
            self.assertEqual(report['progress_error_type'], 'OSError')

    def test_running_evidence_is_visible_and_parseable_before_context_exit(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            manifest, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            manifest.write_text(json.dumps({'task_id': 4, 'student_count': 2}))
            with module.evidence_files(manifest, output) as files:
                files['attempts'].write('{"successful": true}\n')
                files['attempts'].flush()
                self.assertEqual(json.loads(output.with_suffix('.attempts.jsonl').read_text()), {'successful': True})
                self.assertTrue(json.loads(output.read_text())['incomplete'])
                self.assertTrue(json.loads(manifest.with_suffix('.ledger.json').read_text())['incomplete'])

    def test_operation_journal_flushes_safe_metadata_continuously(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            manifest, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            manifest.write_text(json.dumps({'task_id': 5, 'student_count': 2}))
            runner = type('Runner', (), {'ledger': {}, 'operation_measurements': [],
                'protocol_operation_measurements': [], 'lock': threading.Lock()})()
            with module.evidence_files(manifest, output) as files:
                files['runner'] = runner
                runner.operation_measurements.append({'metric': 'start', 'elapsed_seconds': 2,
                    'successful': False, 'http_status': 503, 'password': 'SECRET', 'body': {'answer': 'SECRET'}})
                runner.protocol_operation_measurements.append({'metric': 'enter_item', 'elapsed_seconds': .1,
                    'successful': True, 'student_index': 0, 'lease_token': 'SECRET'})
                rows = [json.loads(line) for line in output.with_suffix('.operations.jsonl').read_text().splitlines()]
                self.assertEqual([row['metric'] for row in rows], ['start', 'enter_item'])
                self.assertNotIn('SECRET', json.dumps(rows))
                self.assertEqual(rows[0]['measurement_source'], 'operation_measurements')

    def test_partial_result_has_actual_counts_and_success_and_failure_distributions(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            manifest, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            manifest.write_text(json.dumps({'task_id': 6, 'student_count': 3}))
            with module.evidence_files(manifest, output) as files:
                files['runner'] = type('Runner', (), {'ledger': {'1': {'complete': True}, '2': {'complete': False}},
                    'request_count': 4, 'request_errors': 1, 'errors': [{'phase': 'start', 'error': 'TimeoutError'}],
                    'operation_measurements': [{'metric': 'start', 'elapsed_seconds': 1, 'successful': True},
                                               {'metric': 'start', 'elapsed_seconds': 7, 'successful': False}],
                    'protocol_operation_measurements': []})()
                files['persist_partial'](signal.SIGTERM)
                partial = json.loads(output.read_text())
                self.assertEqual(partial['completed_student_count'], 1)
                self.assertEqual(partial['incomplete_student_count'], 2)
                self.assertEqual(partial['session_count'], 2)
                self.assertEqual(partial['http_request_count'], 4)
                self.assertEqual(partial['operation_latency']['start']['failed']['p95_seconds'], 7)
                self.assertEqual(partial['operation_latency']['start']['successful']['count'], 1)
                self.assertEqual(partial['errors'][0]['error'], 'TimeoutError')

    def test_sigterm_leaves_worker_journals_open_and_retains_signal_on_final_snapshot(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            manifest, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            manifest.write_text(json.dumps({'task_id': 10, 'student_count': 2}))
            runner = type('Runner', (), {'ledger': {}, 'operation_measurements': [],
                'protocol_operation_measurements': []})()
            with module.evidence_files(manifest, output) as files:
                files['runner'] = runner
                with self.assertRaises(SystemExit):
                    files['interrupt'](signal.SIGTERM, None)
                self.assertFalse(files['attempts'].closed)
                runner.operation_measurements.append({'metric': 'start', 'elapsed_seconds': 3, 'successful': False})
                files['attempts'].write('{"successful": false}\n')
                runner.ledger['11'] = {'complete': False}
            result = json.loads(output.read_text())
            self.assertEqual(result['interruption_signal'], signal.SIGTERM)
            self.assertEqual(result['operation_latency']['start']['failed']['count'], 1)
            self.assertEqual(result['session_count'], 1)
            self.assertFalse(result['passed'])
            self.assertEqual(json.loads(output.with_suffix('.attempts.jsonl').read_text()), {'successful': False})

    def test_progress_snapshot_refreshes_counts_while_work_is_running(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            manifest, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            manifest.write_text(json.dumps({'task_id': 13, 'student_count': 2}))
            runner = type('Runner', (), {'ledger': {}, 'operation_measurements': [],
                'protocol_operation_measurements': []})()
            with module.evidence_files(manifest, output) as files:
                files['runner'] = runner
                runner.ledger['11'] = {'complete': True}
                files['persist_progress']()
                progress = json.loads(output.with_suffix('.progress.json').read_text())
                self.assertEqual(progress['completed_student_count'], 1)
                self.assertFalse(progress['passed'])
                self.assertTrue(progress['incomplete'])

    def test_completed_report_preserves_failed_workflow_statistics(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'fixture.json'
            output = Path(directory) / 'result.json'
            manifest.write_text(json.dumps({'task_id': 9, 'student_count': 2}))
            report = {'workflow_completed': False, 'completed_student_count': 1,
                      'metrics': {'start': {'count': 2, 'p95_seconds': 9}}}
            with module.evidence_files(manifest, output) as files:
                json.dump(report, files['result'])
                files['result_ready'] = True
            self.assertEqual(json.loads(output.read_text()), report)

    def test_sigterm_persists_parseable_partial_result_and_ledger_with_counts(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'fixture.json'
            output = root / 'result.json'
            manifest.write_text(json.dumps({'task_id': 7, 'student_count': 2, 'capture_protocol': module.CONTRACT}))
            with module.evidence_files(manifest, output) as files:
                files['runner'] = type('Runner', (), {
                    'ledger': {'11': {'events': {'e1': {'acknowledged': True}}, 'complete': False}},
                    'workload_version': module.BROWSER_ALIGNED_WORKLOAD,
                    'protocol_operation_measurements': [{'metric': 'login', 'successful': False}],
                    'operation_measurements': [{'metric': 'start', 'successful': True}],
                })()
                with self.assertRaises(SystemExit):
                    files['interrupt'](signal.SIGTERM, None)
            result = json.loads(output.read_text())
            ledger = json.loads(manifest.with_suffix('.ledger.json').read_text())
            self.assertTrue(result['incomplete'])
            self.assertEqual(result['completed_session_count'], 0)
            self.assertEqual(result['interruption_signal'], signal.SIGTERM)
            self.assertTrue(ledger['incomplete'])
            self.assertIn('11', ledger['sessions'])

    def test_interrupted_partial_keeps_prior_valid_output_and_fixture_ledger_is_not_corrupted(self):
        module = load_capture_protocol()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'fixture.json'
            output = root / 'result.json'
            manifest.write_text(json.dumps({'task_id': 8, 'student_count': 1, 'capture_protocol': module.CONTRACT}))
            with module.evidence_files(manifest, output) as files:
                files['runner'] = type('Runner', (), {
                    'ledger': {'12': {'events': {}, 'complete': False}},
                    'workload_version': module.CONTRACT,
                    'protocol_operation_measurements': [],
                    'operation_measurements': [],
                })()
                files['persist_partial'](None)
            result = json.loads(output.read_text())
            ledger = json.loads(manifest.with_suffix('.ledger.json').read_text())
            self.assertTrue(result['incomplete'])
            self.assertEqual(result['completed_session_count'], 0)
            self.assertTrue(ledger['incomplete'])
            self.assertIn('12', ledger['sessions'])
