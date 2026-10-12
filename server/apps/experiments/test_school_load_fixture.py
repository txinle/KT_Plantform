from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class SchoolLoadFixtureTests(SimpleTestCase):
    def test_seed_rejects_non_isolated_database_before_reading_password(self):
        with self.assertRaisesRegex(CommandError, "kt_e2e"):
            call_command("seed_school_mixed_load", password_file="not-read", output_dir="not-created", stdout=StringIO())
