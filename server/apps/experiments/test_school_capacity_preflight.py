import os
from unittest.mock import patch

from django.test import SimpleTestCase

from .management.commands.report_300_capacity import capacity_report


class SchoolCapacityPreflightTests(SimpleTestCase):
    def test_six_pool_report_distinguishes_observed_role_and_configured_total(self):
        with patch.dict(os.environ, {
            "SCHOOL_RUNTIME_ISOLATION_ENABLED": "1", "SCHOOL_POOL_PROFILE": "six-pools-180-v1",
            "GUNICORN_WORKERS": "24", "WEB_REPLICAS": "1", "POSTGRES_HOST": "pgbouncer",
            "POSTGRES_POOL_ALIAS": "kt_assessment", "DATABASE_APPLICATION_NAME": "kt-school-assessment",
        }):
            report = capacity_report()
        self.assertTrue(report["ready_for_measurement"])
        self.assertEqual(report["role_workers"], {"core": 1, "auth": 4, "assessment": 24,
            "capture": 4, "student_read": 2, "teacher_read": 1})
        self.assertEqual(report["configured_http_worker_total"], 36)
        self.assertEqual(report["observed_role_workers"], 24)
        self.assertFalse(report["other_role_containers_observed"])
        self.assertFalse(report["accepted_for_300_users"])

    def test_six_pool_old_eight_worker_assessment_is_not_ready(self):
        with patch.dict(os.environ, {
            "SCHOOL_RUNTIME_ISOLATION_ENABLED": "1", "SCHOOL_POOL_PROFILE": "six-pools-180-v1",
            "GUNICORN_WORKERS": "8", "WEB_REPLICAS": "1", "POSTGRES_HOST": "pgbouncer",
            "POSTGRES_POOL_ALIAS": "kt_assessment",
        }):
            self.assertFalse(capacity_report()["ready_for_measurement"])

    def test_six_pool_wrong_observed_worker_count_is_not_ready(self):
        with patch.dict(os.environ, {
            "SCHOOL_RUNTIME_ISOLATION_ENABLED": "1", "SCHOOL_POOL_PROFILE": "six-pools-180-v1",
            "GUNICORN_WORKERS": "12", "POSTGRES_HOST": "pgbouncer", "POSTGRES_POOL_ALIAS": "kt_core",
        }):
            self.assertFalse(capacity_report()["ready_for_measurement"])

    def test_fixed_twelve_worker_candidate_is_ready_to_measure_not_certified(self):
        with patch.dict(os.environ, {
            "SCHOOL_RUNTIME_ISOLATION_ENABLED": "1", "GUNICORN_WORKERS": "12",
            "WEB_REPLICAS": "1", "POSTGRES_HOST": "pgbouncer",
        }):
            report = capacity_report()
        self.assertTrue(report["ready_for_measurement"])
        self.assertEqual(report["role_workers"], {"core": 12, "student_read": 2, "teacher_read": 1})
        self.assertFalse(report["accepted_for_300_users"])

    def test_old_worker_setting_is_not_the_revised_measurement_configuration(self):
        with patch.dict(os.environ, {
            "SCHOOL_RUNTIME_ISOLATION_ENABLED": "1", "GUNICORN_WORKERS": "8",
            "WEB_REPLICAS": "1", "POSTGRES_HOST": "pgbouncer",
        }):
            self.assertFalse(capacity_report()["ready_for_measurement"])
