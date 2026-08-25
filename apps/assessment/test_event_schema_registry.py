from importlib.metadata import PackageNotFoundError, version

from django.test import SimpleTestCase


class JsonSchemaDependencyTests(SimpleTestCase):
    def test_supported_jsonschema_version_is_installed(self):
        try:
            installed_version = version("jsonschema")
        except PackageNotFoundError:
            installed_version = None

        self.assertEqual(installed_version, "4.26.0")
