from django.conf import settings
from django.test import SimpleTestCase


class ProductionRequestLoggingTests(SimpleTestCase):
    def test_django_request_errors_are_sent_to_console(self):
        logger = settings.LOGGING["loggers"]["django.request"]

        self.assertIn("console", logger["handlers"])
        self.assertEqual(logger["level"], "ERROR")
