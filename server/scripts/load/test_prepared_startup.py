import copy
import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class PreparedStartupEvaluatorTests(unittest.TestCase):
    def evaluator(self):
        self.assertIsNotNone(importlib.util.find_spec('prepared_startup'), 'Prepared startup evaluator is missing')
        import prepared_startup
        return prepared_startup

    def plan(self):
        scenarios = [{'scenario_id': f's{i}', 'planned_startups': [
            {'attempt_id': f'a{i}', 'task_id': 1, 'transport': 'browser', 'cache_state': 'cold'},
            {'attempt_id': f'b{i}', 'task_id': 2, 'transport': 'protocol', 'cache_state': 'warm'}],
            'planned_saves': [{'operation_id': f'v{i}', 'task_id': 1}]} for i in range(5)]
        return self.evaluator().build_plan(scenarios, settings={'arrival_seconds': 30}, run_ids=['r01', 'r02'])

    def witness(self, attempt_id='a0', transport='browser', run_id='r01', scenario_id='s0', task_id=1):
        return {'run_id': run_id, 'scenario_id': scenario_id, 'attempt_id': attempt_id, 'task_id': task_id,
            'input_settings_hash': self.plan()['input_settings_hash'],
            'transport': transport, 'status': 'completed', 'phase_origin_id': 'browser-time-origin-1',
            'timestamps_ms': dict(zip(self.evaluator().PHASES, [0, 10, 210, 220, 320, 330, 430, 530, 630])),
            'activation_receipt_ids': ['session-001'], 'activation_call_count': 1}

    def test_all_planned_missing_attempts_and_saves_remain_in_denominator(self):
        result = self.evaluator().evaluate(self.plan(), [self.witness()], [])
        self.assertEqual(result['startups']['planned'], 20)
        self.assertEqual(result['startups']['completed'], 1)
        self.assertEqual(result['startups']['missing'], 19)
        self.assertEqual(result['mixed_saves']['planned'], 10)
        self.assertEqual(result['mixed_saves']['missing'], 10)
        self.assertIsNone(result['capacity_verdict'])
        self.assertEqual(result['browser_operability']['completed'], 1)

    def test_phase_durations_and_percentiles_are_from_one_monotonic_origin(self):
        result = self.evaluator().evaluate(self.plan(), [self.witness()], [])
        phases = result['browser_operability']['durations_ms']
        self.assertEqual(phases['queue']['p95'], 200)
        self.assertEqual(phases['activation']['p99'], 100)
        self.assertEqual(phases['first_transfer']['max'], 100)
        self.assertEqual(phases['initialization']['p50'], 100)
        self.assertEqual(phases['click_to_operable']['p95'], 630)
        self.assertEqual(result['fairness_per_task']['1']['planned'], 10)
        self.assertEqual(result['fairness_per_task']['2']['completed'], 0)

    def test_direct_start_requires_real_request_response_phases_without_inventing_queue_time(self):
        evaluator = self.evaluator()
        old = self.plan()
        plan = evaluator.build_plan(old['scenarios'], settings={**old['settings'], 'startup_protocol': 'direct'}, run_ids=old['run_ids'])
        row = self.witness()
        row['input_settings_hash'] = plan['input_settings_hash']
        row['startup_protocol'] = 'direct'
        del row['timestamps_ms']['admission_granted']
        del row['timestamps_ms']['activation_started']
        result = evaluator.evaluate(plan, [row], [])
        self.assertEqual(result['browser_operability']['completed'], 1)
        self.assertIsNone(result['browser_operability']['durations_ms']['queue']['p95'])
        self.assertEqual(result['browser_operability']['durations_ms']['activation']['p95'], 310)

    def test_protocol_completion_never_counts_as_browser_operability(self):
        result = self.evaluator().evaluate(self.plan(), [self.witness('b0', 'protocol', task_id=2)], [])
        self.assertEqual(result['startups']['completed'], 1)
        self.assertEqual(result['browser_operability']['completed'], 0)
        self.assertEqual(result['protocol_startups']['completed'], 1)

    def test_failed_incomplete_duplicate_and_unknown_evidence_is_accounted(self):
        records = [self.witness(), self.witness(), self.witness('b0', 'protocol', task_id=2)]
        records[2]['status'] = 'failed'
        records[2]['error_code'] = 'activation_503'
        records.append(self.witness('unknown'))
        result = self.evaluator().evaluate(self.plan(), records, [])
        self.assertEqual(result['startups']['failed'], 2)
        self.assertEqual(result['duplicate_startup_records'], 1)
        self.assertEqual(result['unknown_startup_records'], 1)
        bad = self.witness()
        bad['timestamps_ms']['operable'] = 200
        result = self.evaluator().evaluate(self.plan(), [bad], [])
        self.assertEqual(result['startups']['incomplete'], 1)

    def test_duplicate_effective_activation_is_not_hidden_by_a_fast_witness(self):
        row = self.witness()
        row['activation_receipt_ids'].append('session-002')
        result = self.evaluator().evaluate(self.plan(), [row], [])
        self.assertEqual(result['duplicate_effective_activations'], 1)
        self.assertEqual(result['startups']['failed'], 1)

    def test_settings_and_scenario_plan_hash_detect_drift(self):
        plan = self.plan()
        altered = copy.deepcopy(plan)
        altered['settings']['arrival_seconds'] = 0
        with self.assertRaises(ValueError):
            self.evaluator().evaluate(altered, [], [])
        self.assertEqual(self.plan()['input_settings_hash'], plan['input_settings_hash'])

    def test_mixed_saves_include_failures_missing_and_success_latency(self):
        saves = [{'run_id': 'r01', 'scenario_id': 's0', 'operation_id': 'v0', 'task_id': 1,
                  'input_settings_hash': self.plan()['input_settings_hash'],
                  'status': 'completed', 'started_at_ms': 20, 'completed_at_ms': 75},
                 {'run_id': 'r02', 'scenario_id': 's0', 'operation_id': 'v0', 'task_id': 1,
                  'input_settings_hash': self.plan()['input_settings_hash'],
                  'status': 'failed', 'error_code': 'version_conflict'}]
        result = self.evaluator().evaluate(self.plan(), [], saves)
        self.assertEqual(result['mixed_saves']['completed'], 1)
        self.assertEqual(result['mixed_saves']['failed'], 1)
        self.assertEqual(result['mixed_saves']['missing'], 8)
        self.assertEqual(result['mixed_saves']['durations_ms']['p99'], 55)

    def test_success_from_a_different_settings_hash_cannot_satisfy_the_run(self):
        witness = self.witness()
        witness['input_settings_hash'] = 'f' * 64
        result = self.evaluator().evaluate(self.plan(), [witness], [])
        self.assertEqual(result['startups']['completed'], 0)
        self.assertEqual(result['startups']['incomplete'], 1)

    def test_repeat_run_uses_its_own_fresh_roster_and_task_bindings(self):
        plan = self.plan()
        scenarios = copy.deepcopy(plan['scenarios'])
        first = scenarios[0]
        first['run_bindings'] = {
            'r01': {'planned_startups': first['planned_startups'], 'planned_saves': first['planned_saves']},
            'r02': {'planned_startups': [{'attempt_id': 'fresh-r02', 'task_id': 9,
                                        'transport': 'browser', 'cache_state': 'cold'}], 'planned_saves': []}}
        frozen = self.evaluator().build_plan(scenarios, settings=plan['settings'], run_ids=plan['run_ids'])
        result = self.evaluator().evaluate(frozen, [], [])
        self.assertEqual(result['startups']['planned'], 19)
        self.assertEqual(result['fairness_per_task']['9']['planned'], 1)
        self.assertEqual(result['mixed_saves']['planned'], 9)

    def test_resource_unexecuted_witness_is_separate_from_measured_failure(self):
        record = self.witness()
        record.update(status='not_executed', error_code='local_browser_resource_budget')
        result = self.evaluator().evaluate(self.plan(), [record], [])
        self.assertEqual(result['startups']['planned'], 20)
        self.assertEqual(result['startups']['failed'], 0)
        self.assertEqual(result['startups']['not_executed'], 1)

    def test_resource_pilot_keeps_180_denominator_without_inventing_other_cells(self):
        scenario = {'scenario_id': 'cold-burst', 'planned_startups': [
            {'attempt_id': f'a{i}', 'task_id': 1, 'transport': 'browser', 'cache_state': 'cold'} for i in range(180)],
            'planned_saves': []}
        try:
            plan = self.evaluator().build_plan([scenario], settings={'stage': 'resource_pilot', 'C': 1, 'R': 2}, run_ids=['pilot01'])
        except ValueError as error:
            self.fail(f'An explicitly declared resource pilot must be accepted: {error}')
        result = self.evaluator().evaluate(plan, [], [])
        self.assertEqual(result['startups']['planned'], 180)
        self.assertIsNone(result['capacity_verdict'])

    def fixture(self, run='fresh', active=0):
        return {'isolated_demo': True, 'database': 'prepared_e2e', 'settings_binding': {
            'ASSESSMENT_ADMISSION_CAPACITY': 2, 'ASSESSMENT_ADMISSION_RATE_PER_SECOND': 8}, 'accounts': [
                {'account_id': f'{run}-{index}', 'username': f'{run}-{index}', 'password': 'private-only',
                 'taskId': 1, 'courseId': 1, 'session_id': index + 1, 'initial_state': 'active' if index < active else 'prepared',
                 'question_url': f'/student/sessions/{index + 1}/questions/1/' if index < active else None,
                 'save_value': 'A' if index < active else None, 'warm_urls': ['/content/manifest', '/content/q1'],
                 'questions': [{'assignment_id': 1, 'question_type': 'single_choice'},
                               {'assignment_id': 2, 'question_type': 'multiple_choice'}]} for index in range(180)]}

    def test_fixture_builder_keeps_full_pilot_and_private_credentials_separate(self):
        module = self.evaluator()
        self.assertTrue(hasattr(module, 'build_fixture_plan'))
        plan, accounts = module.build_fixture_plan([{'scenario_id': 'cold-burst', 'run_id': 'pilot01',
            'fixture': self.fixture()}], settings={'stage': 'resource_pilot', 'C': 2, 'R': 8}, run_ids=['pilot01'])
        rows = module.run_binding(plan['scenarios'][0], 'pilot01')['planned_startups']
        self.assertEqual(len(rows), 180)
        self.assertEqual(len(accounts['accounts']), 180)
        self.assertNotIn('private-only', str(plan))
        with self.assertRaises(ValueError):
            module.build_fixture_plan([{'scenario_id': 'cold-burst', 'run_id': 'pilot01',
                'fixture': self.fixture()}], settings={'stage': 'resource_pilot', 'C': 1, 'R': 8}, run_ids=['pilot01'])

    def test_mixed_and_reload_actions_are_planned_and_missing_action_evidence_is_counted(self):
        module = self.evaluator()
        self.assertTrue(hasattr(module, 'build_fixture_plan'))
        plan, _ = module.build_fixture_plan([{'scenario_id': 'mixed', 'run_id': 'r01',
            'fixture': self.fixture(active=60)}], settings={'stage': 'resource_pilot', 'C': 2, 'R': 8}, run_ids=['r01'])
        roster = module.run_binding(plan['scenarios'][0], 'r01')
        self.assertEqual(len(roster['planned_startups']), 120)
        self.assertEqual(len(roster['planned_saves']), 60)
        self.assertEqual([row['kind'] for row in roster['planned_saves'][0]['actions']], ['save', 'switch', 'save', 'submit'])
        result = module.evaluate(plan, [], [])
        self.assertEqual(result['mixed_actions']['planned'], 240)
        self.assertEqual(result['mixed_actions']['missing'], 240)
        self.assertIsNone(result['mixed_actions']['draft_http_ms']['p95'])

    def test_save_http_timing_excludes_editing_debounce_and_recovery_is_separate(self):
        module = self.evaluator()
        plan = self.plan()
        scenario = copy.deepcopy(plan['scenarios'])
        scenario[0]['planned_saves'][0]['actions'] = [{'operation_id': 'edit1', 'kind': 'save'}]
        scenario[0]['planned_startups'][0]['post_start_actions'] = [{'operation_id': 'reload1', 'kind': 'reload'}]
        plan = module.build_plan(scenario, settings=plan['settings'], run_ids=plan['run_ids'])
        startup = self.witness()
        startup['input_settings_hash'] = plan['input_settings_hash']
        save = {'run_id': 'r01', 'scenario_id': 's0', 'operation_id': 'v0', 'task_id': 1,
            'input_settings_hash': plan['input_settings_hash'], 'status': 'completed',
            'started_at_ms': 0, 'completed_at_ms': 1000,
            'action_results': [{'operation_id': 'edit1', 'kind': 'save', 'status': 'completed',
                'started_at_ms': 0, 'completed_at_ms': 1000, 'editing_experience_ms': 1000, 'draft_http_ms': 80}]}
        result = module.evaluate(plan, [startup], [save])
        self.assertEqual(result['mixed_actions']['draft_http_ms']['p95'], 80)
        self.assertEqual(result['mixed_actions']['editing_experience_ms']['p95'], 1000)
        self.assertEqual(result['recovery_actions']['missing'], 2)
        self.assertEqual(result['startups']['completed'], 1)

    def test_declared_reload_success_requires_actual_ui_and_server_pointer_witness(self):
        module = self.evaluator()
        scenarios = copy.deepcopy(self.plan()['scenarios'])
        scenarios[0]['planned_startups'][0]['post_start_actions'] = [{'operation_id': 'reload1', 'kind': 'reload'}]
        plan = module.build_plan(scenarios, settings={}, run_ids=['r01', 'r02'])
        startup = self.witness()
        startup.update(input_settings_hash=plan['input_settings_hash'], action_results=[{
            'operation_id': 'reload1', 'kind': 'reload', 'status': 'completed'}])
        result = module.evaluate(plan, [startup], [])
        self.assertEqual(result['recovery_actions']['completed'], 0)
        self.assertEqual(result['recovery_actions']['incomplete'], 1)

    def test_build_cli_writes_bound_plan_and_refuses_output_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'fixture.json').write_text(json.dumps(self.fixture()), encoding='utf-8')
            (folder / 'cells.json').write_text(json.dumps([{'scenario_id': 'cold-burst',
                'run_id': 'pilot01', 'fixture': 'fixture.json'}]), encoding='utf-8')
            (folder / 'settings.json').write_text(json.dumps({'stage': 'resource_pilot', 'C': 2, 'R': 8}), encoding='utf-8')
            command = [sys.executable, str(Path(__file__).with_name('prepared_startup.py')),
                '--cells', str(folder / 'cells.json'), '--settings', str(folder / 'settings.json'),
                '--run-ids', 'pilot01', '--output', str(folder / 'plan.json'),
                '--accounts-output', str(folder / 'private.json')]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['plan_sha256'],
                hashlib.sha256((folder / 'plan.json').read_bytes()).hexdigest())
            plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
            self.assertEqual(len(plan['settings']['harness_source_sha256']), 2)
            self.assertEqual(self.evaluator().evaluate(plan, [], [])['startups']['planned'], 180)
            self.assertNotIn('private-only', (folder / 'plan.json').read_text(encoding='utf-8'))
            repeated = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(repeated.returncode, 0)


if __name__ == '__main__':
    unittest.main()
