import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import capacity_teacher_v4 as v4


class TeacherCapacityV4Tests(unittest.TestCase):
    def manifest(self):
        return v4.default_manifest("a" * 64)

    def test_identity_refuses_old_project_ports_and_volume_reuse(self):
        value = self.manifest()
        self.assertIs(v4.validate_manifest(value), value)
        for key, wrong in (("project", "kt-school-e2e-capacity-retest-20261016"),
                           ("root", "/home/ktdata/student-capacity-tests/20261016"),
                           ("port", 18085), ("database", "kt_platform"),
                           ("data_origin", "clone_old_volumes")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                v4.validate_manifest(value | {key: wrong})

    def test_matrix_has_qualification_and_exact_entry_full_flow_rounds(self):
        cells = v4.matrix(self.manifest())
        self.assertEqual(len([c for c in cells if c["stage"] == "qualification"]), 1)
        self.assertEqual(len([c for c in cells if c["stage"] == "baseline"]), 12)
        self.assertEqual(len([c for c in cells if c["stage"] == "candidate-entry"]), 24)
        self.assertEqual(len([c for c in cells if c["stage"] == "candidate-full-flow"]), 8)
        self.assertTrue(all(c["count"] == (50 if c["stage"] == "qualification" else 180) for c in cells))
        self.assertTrue(all(c["browser_count"] == 2 for c in cells))

    def test_arrivals_preserve_full_roster_positions_for_browser_protocol_split(self):
        roster = [f"s{i}" for i in range(180)]
        rows = v4.arrivals(roster, [roster[0], roster[-1]], window_seconds=60)
        self.assertEqual([r["login_offset_ms"] for r in rows], [60000 * i / 179 for i in range(180)])
        self.assertEqual(sum(r["driver"] == "browser" for r in rows), 2)
        self.assertGreater(rows[1]["login_offset_ms"], 0)
        self.assertLess(rows[-2]["login_offset_ms"], 60000)

    def test_entry_teacher_metrics_are_descriptive_without_tail_claim(self):
        rows = [{"category": key, "success": True, "duration_ms": 12,
                 "during_student_pressure": True} for key in v4.TEACHER_LIMITS]
        report = v4.teacher_metrics(rows, descriptive=True)
        self.assertIsNone(report["accepted"])
        self.assertEqual(report["criterion"], "descriptive_only_entry_window")
        self.assertEqual(report["metrics"]["status"]["max_ms"], 12)
        self.assertEqual(report["raw_durations_ms"]["status"], [12])
        self.assertNotIn("p95_ms", report["metrics"]["status"])
        self.assertEqual(report["request_failures"], 0)
        rows[0]["success"] = False
        self.assertEqual(v4.teacher_metrics(rows, descriptive=True)["request_failures"], 1)
        self.assertFalse(v4.teacher_metrics(rows)["accepted"])

    def test_teacher_tail_does_not_accept_samples_after_student_pressure(self):
        rows = [{"category": "advanced", "success": True, "duration_ms": 1,
                 "during_student_pressure": False} for _ in range(108)]
        report = v4.teacher_metrics(rows)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["metrics"]["advanced"]["planned"], 0)
        self.assertEqual(report["after_pressure_samples"], 108)

    def test_formal_acceptance_fails_closed_for_missing_denominator_and_monitor(self):
        cell = v4.matrix(self.manifest())[1]
        report = v4.evaluate(cell, [], [], {"integrity_passed": True}, {"monitor_ok": True})
        self.assertFalse(report["passed"])
        self.assertIn("student_denominator_incomplete", report["issues"])
        self.assertIn("measurement_invalid", report["issues"])

    def test_mixed_comparison_requires_matching_environment_and_individual_metrics(self):
        reference = {"binding": "same", "timings": {k: {"p95_ms": 100} for k in ("save", "navigation", "submission")}}
        candidate = copy.deepcopy(reference)
        candidate["timings"]["navigation"]["p95_ms"] = 111
        self.assertFalse(v4.mixed_comparison(reference, candidate)["passed"])
        candidate["timings"]["navigation"]["p95_ms"] = 110
        self.assertTrue(v4.mixed_comparison(reference, candidate)["passed"])
        candidate["binding"] = "different"
        self.assertFalse(v4.mixed_comparison(reference, candidate)["passed"])

    def test_private_fixture_never_enters_public_matrix(self):
        text = json.dumps(v4.matrix(self.manifest()))
        for secret in ("password", "cookie", "token", "csrf"):
            self.assertNotIn(secret, text)

    def test_fixture_builder_has_its_own_bounded_preload_memory(self):
        import prepare_teacher_capacity_v4 as prepare
        source = {"services": {"db": {"environment": {}}, "web": {
            "image": "old", "environment": {}, "mem_limit": 512 * 1024**2,
            "healthcheck": {"test": ["CMD", "true"]}, "ports": []}}, "volumes": {}, "networks": {}}
        result = prepare.rebind_compose(source, self.manifest(), phase="candidate")
        fixture = result["services"]["fixture-builder"]
        self.assertEqual(fixture["mem_limit"], 2 * 1024**3)
        self.assertEqual(fixture["cgroup_parent"], "ktteachercapacity-app.slice")
        self.assertEqual(fixture["profiles"], ["fixture"])
        self.assertEqual(fixture["entrypoint"], ["python", "manage.py"])
        self.assertEqual(fixture["environment"]["POSTGRES_HOST"], "db")
        self.assertNotIn("healthcheck", fixture)
        self.assertEqual(result["services"]["web"]["mem_limit"], 512 * 1024**2)

    def test_controller_prepares_fixture_outside_live_web_container(self):
        import control_teacher_capacity_v4 as controller
        owned = object.__new__(controller.Controller)
        owned.compose = Mock()
        owned.prepare_fixture("candidate", "a" * 20, "/app/media/teacher-capacity-v4/" + "a" * 20)
        args = owned.compose.call_args.args
        self.assertIn("fixture-builder", args)
        self.assertEqual(args[:5], ("candidate", "run", "--rm", "--no-deps", "-T"))

    def test_compose_rebinds_every_volume_and_includes_cleanup_worker_budget(self):
        import prepare_teacher_capacity_v4 as prepare
        source = {"name": "unused", "services": {
            "db": {"environment": {"POSTGRES_DB": "kt_e2e"}},
            "web": {"image": "old", "environment": {}, "volumes": [{"type": "volume", "source": "media_data", "target": "/app/media"}]},
            "nginx": {"ports": []},
        }, "volumes": {"postgres_data": {}, "media_data": {}}, "networks": {"default": {}}}
        result = prepare.rebind_compose(source, self.manifest(), phase="candidate")
        self.assertEqual(result["name"], v4.PROJECT)
        self.assertEqual(result["volumes"]["postgres_data"]["name"], v4.PROJECT + "_candidate_postgres_data")
        worker = result["services"]["artifact-cleanup-worker"]
        self.assertIn("run_research_artifact_cleanups", worker["command"])
        self.assertEqual(worker["environment"]["POSTGRES_HOST"], "db")
        self.assertEqual(worker["cgroup_parent"], "ktteachercapacity-app.slice")
        source["volumes"]["media_data"]["external"] = True
        with self.assertRaisesRegex(ValueError, "external"):
            prepare.rebind_compose(source, self.manifest(), phase="candidate")

    def test_frozen_plan_preserves_windows_and_complete_twenty_question_trajectory(self):
        from test_student_capacity_protocol_v3 import fixture
        cell = next(c for c in v4.matrix(self.manifest()) if c["stage"] == "candidate-full-flow")
        plan = v4.build_plan(self.manifest(), cell, fixture(), "f" * 64)
        self.assertEqual(plan["version"], "student-capacity-protocol-v3")
        self.assertEqual(plan["capacity_version"], v4.VERSION)
        self.assertEqual(plan["roster"][-1]["login_offset_ms"], 10000)
        self.assertEqual(plan["workload_duration_ms"], 600000)
        self.assertTrue(all(len(r["answers"]) == 20 for r in plan["roster"]))
        self.assertEqual(plan["plan_id"], v4.digest({k: v for k, v in plan.items() if k != "plan_id"}))

    def test_release_refuses_stale_or_incomplete_monitor_and_wrong_project(self):
        import run_teacher_capacity_v4 as runner
        valid = {"project": v4.PROJECT, "updated_at": 100, "monitor_ok": True,
                 "non_project_unchanged": True, "oom": False, "memory_available_bytes": 3 * 1024**3}
        self.assertTrue(runner.monitor_valid(valid, now=110))
        for changed in ({"updated_at": 0}, {"project": "production"}, {"oom": True},
                        {"monitor_ok": False}, {"non_project_unchanged": False},
                        {"memory_available_bytes": 1024}):
            self.assertFalse(runner.monitor_valid(valid | changed, now=110))

    def test_cold_protocol_does_not_bootstrap_before_shared_release(self):
        import threading
        import run_teacher_capacity_v4 as runner
        actor = object.__new__(runner.Actor)
        actor.plan, actor.record, actor.csrf = {"bootstrap": "cold"}, {}, Mock()
        actor.prepare(threading.BoundedSemaphore(1))
        actor.csrf.assert_not_called()
        actor.plan["bootstrap"] = "warm"
        actor.prepare(threading.BoundedSemaphore(1))
        actor.csrf.assert_called_once()
        self.assertTrue(actor.record["anonymous_csrf_prepared"])

    def test_cold_bootstrap_time_is_not_mislabeled_as_generator_lateness(self):
        import run_teacher_capacity_v4 as runner
        actor = object.__new__(runner.Actor)
        actor.record, actor.csrf = {"release_skew_ms": 7}, Mock()
        def native_login(_actor):
            _actor.record["release_skew_ms"] = 807
        with patch.object(runner.protocol.Actor, "login", native_login):
            actor.login()
        self.assertEqual(actor.record["release_skew_ms"], 7)
        self.assertEqual(actor.record["cold_first_post_delay_ms"], 800)

    def test_foreign_inventory_preserves_nonproject_lifetimes(self):
        import observe_teacher_capacity_v4 as observer
        item = {"Id": "a", "Image": "image", "State": {"Running": True, "StartedAt": "now"},
                "Config": {"Labels": {"com.docker.compose.project": "production"}}}
        owned = copy.deepcopy(item)
        owned["Config"]["Labels"]["com.docker.compose.project"] = v4.PROJECT
        self.assertEqual(observer.foreign_signature([item, owned]), observer.foreign_signature([item]))
        changed = copy.deepcopy(item);changed["RestartCount"] = 1
        self.assertNotEqual(observer.foreign_signature([item]), observer.foreign_signature([changed]))

    def test_pack_whitelist_excludes_credentials_runtime_and_temp_artifacts(self):
        import build_teacher_capacity_v4 as pack
        for path in ("server/.env", "deploy/school-test.env", "server/media/a.json", "client/node_modules/x.js",
                     ".tmp-test.log", "server/private-key.pem", "client/test-results/a.png", "server/db.sqlite3"):
            with self.subTest(path=path):
                self.assertFalse(pack.allowed_source(path))
        for path in ("server/apps/analytics/data_scope.py", "client/src/main.ts", "deploy/Dockerfile.teacher-capacity-driver"):
            self.assertTrue(pack.allowed_source(path))

    def test_controller_preserves_bounded_command_error_detail(self):
        import control_teacher_capacity_v4 as controller
        failed = Mock(returncode=1, stdout='', stderr='C4 fixture validation failed')
        with patch.object(controller.subprocess, 'run', return_value=failed):
            with self.assertRaisesRegex(RuntimeError, 'C4 fixture validation failed'):
                controller.call(['docker', 'exec', 'owned', 'python', 'manage.py'])

    def test_controller_defaults_only_to_qualification(self):
        import control_teacher_capacity_v4 as controller
        self.assertEqual(controller.select_cells(self.manifest(), "qualification"), [v4.matrix(self.manifest())[0]])
        self.assertEqual(len(controller.select_cells(self.manifest(), "baseline")), 12)
        with self.assertRaises(ValueError):
            controller.select_cells(self.manifest(), "old-controller")


if __name__ == "__main__":
    unittest.main()
