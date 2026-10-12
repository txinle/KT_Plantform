import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase


class SchoolAuthenticationPacingTests(SimpleTestCase):
    def runner(self):
        sys.path.insert(0, str(Path(settings.BASE_DIR) / 'scripts/load'))
        import assessment_300
        return assessment_300, assessment_300.Runner('http://localhost', 1, 35)

    def test_one_hundred_logins_complete_with_six_workers_and_no_start_barrier(self):
        module, runner = self.runner()
        active = maximum = completed = 0
        lock = threading.Lock()

        def login(state):
            nonlocal active, maximum, completed
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.002)
            with lock:
                active -= 1
                completed += 1

        with patch.object(module.threading, 'Barrier') as barrier:
            runner.phase('login', list(range(100)), login, 6, synchronized=False)
            barrier.assert_not_called()
        self.assertEqual(completed, 100)
        self.assertGreater(maximum, 1)
        self.assertLessEqual(maximum, 6)
        self.assertEqual(runner.errors, [])

    def test_default_phase_preserves_real_three_hundred_student_barrier(self):
        module, runner = self.runner()
        completed = []
        real_barrier = threading.Barrier
        with patch.object(module.threading, 'Barrier', wraps=real_barrier) as barrier:
            runner.phase('start', list(range(300)), completed.append, 300)
            barrier.assert_called_once_with(300)
        self.assertEqual(sorted(completed), list(range(300)))
        self.assertEqual(runner.errors, [])

    def test_history_preparation_orders_real_phases_without_concurrency_claim(self):
        module, _ = self.runner()
        runner = Mock(errors=[])
        states = list(range(300))
        module.run_phases(runner, states, 300, fixture_preparation=True)
        self.assertEqual([call.args[0] for call in runner.phase.call_args_list], ['login', 'start', 'answer'])
        self.assertTrue(all(call.args[3] == 6 and call.kwargs == {'synchronized': False}
                            for call in runner.phase.call_args_list))

    def test_default_harness_start_and_answer_keep300_synchronized_students(self):
        module, _ = self.runner()
        runner = Mock(errors=[])
        states = list(range(300))
        module.run_phases(runner, states, 300)
        self.assertEqual(runner.phase.call_args_list[0].args[3], 6)
        for call in runner.phase.call_args_list[1:]:
            self.assertEqual(call.args[3], 300)
            self.assertEqual(call.kwargs, {'synchronized': True})

    def test_baseline_observation_must_cover_authentication_and_start(self):
        self.runner()
        import evaluate_school_load
        phase = {'warmup_started_at': 1, 'started_at': 10, 'ended_at': 1810}
        observed = {'started_at': 1, 'ended_at': 1811, 'source': 'docker_events', 'oom_kills': 0,
                    'swap_start_bytes': 0, 'swap_end_bytes': 0, 'memory_cpu_samples': [{'at': 5}],
                    'backlog_samples': [{'counts': {'research': 0}}]}
        self.assertTrue(all(evaluate_school_load.baseline_checks(phase, observed).values()))
        self.assertFalse(evaluate_school_load.baseline_checks(phase, {**observed, 'started_at': 2})['baseline_observation_covers_auth_and_workload'])
        self.assertFalse(evaluate_school_load.baseline_checks(phase, {**observed, 'oom_kills': 1})['baseline_zero_oom'])
