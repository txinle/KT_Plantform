from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class SeedE2ECaptureInitializationCommandTests(SimpleTestCase):
    def test_refuses_a_non_e2e_database(self):
        with self.assertRaisesRegex(CommandError, "only runs against kt_e2e"):
            call_command("seed_e2e_capture_initialization", run_key="chromium")
