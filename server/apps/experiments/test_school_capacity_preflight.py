import os
from unittest.mock import patch

from django.test import SimpleTestCase

from .management.commands.report_300_capacity import capacity_report


class SchoolCapacityPreflightTests(SimpleTestCase):
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
