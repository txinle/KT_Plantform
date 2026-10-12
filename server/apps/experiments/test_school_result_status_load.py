import importlib.util
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase


class SchoolResultStatusLoadTests(SimpleTestCase):
    def runner(self, statuses):
        path = Path(settings.BASE_DIR) / "scripts/load/school_mixed_300.py"
        spec = importlib.util.spec_from_file_location("school_result_status_load", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        runner = mod.MixedRunner("http://localhost", 1, 35, course_id=2, experiment_id=3, question_id=4)
        calls, recorded = [], {}
        initial = {
            "comparison_status": "pending", "completed_at": "2026-10-10T00:00:00+00:00",
            "comparison_generated_at": "2026-10-10T00:00:20+00:00",
            "items": [{"question_id": 44}],
        }
        pending = iter(statuses)
        def request(state, path, **kwargs):
            calls.append(path)
            status = next(pending) if "status_only" in path else initial["comparison_status"]
            if status == "ready":
                initial["comparison_status"] = "ready"
            return 200, {**initial, "comparison_status": status}, .05
        runner.request = request
        runner.record = lambda key, value: recorded.setdefault(key, []).append(value)
        return mod, runner, calls, recorded

    def test_pending_results_check_metadata_then_reload_once(self):
        mod, runner, calls, recorded = self.runner(["pending", "ready"])
        with patch.object(mod.time, "sleep"), patch.object(mod.time, "monotonic", side_effect=range(0, 1000, 10)):
            runner.after_finish(object())
        metadata = [path for path in calls if "status_only=1" in path]
        full = [path for path in calls if path == "/api/v1/student/tasks/1/results/"]
        self.assertEqual(len(metadata), 2)
        # Initial read, ready read and two explicit repeat visits for hot timings.
        self.assertEqual(len(full), 4)
        self.assertEqual(recorded["comparison_ready"], [20])
        # The retained manifest contains a bank question ID (4), while the
        # completed result supplies the immutable snapshot identity (44).
        details = [path for path in calls if "/questions/" in path]
        self.assertEqual(details, ["/api/v1/student/courses/2/learning/experiments/3/questions/44/"] * 3)

    def test_unready_result_stops_after_three_lightweight_checks(self):
        mod, runner, calls, _ = self.runner(["pending"] * 3)
        with patch.object(mod.time, "sleep"), patch.object(mod.time, "monotonic", side_effect=range(0, 1000, 10)):
            with self.assertRaises(RuntimeError):
                runner.after_finish(object())
        self.assertEqual(len([path for path in calls if "status_only=1" in path]), 3)
        self.assertEqual(calls.count("/api/v1/student/tasks/1/results/"), 1)

    def test_answering_does_not_query_completed_learning_after_every_item(self):
        _, runner, calls, _ = self.runner([])
        runner.after_item(object())
        self.assertEqual(calls, [])
