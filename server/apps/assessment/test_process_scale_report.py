from django.test import TestCase
from django.core.management import call_command
from io import StringIO


class ProcessScaleReportTests(TestCase):
    def test_scale_report_has_thresholds_and_domain_counts(self):
        output = StringIO()
        call_command("process_scale_report", stdout=output)
        text = output.getvalue()
        self.assertIn("partition_threshold_events=20000000", text)
        self.assertIn("assessment_events", text)
        self.assertIn("telemetry_events", text)
