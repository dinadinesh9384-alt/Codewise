from django.test import SimpleTestCase
from .support import module


class ConfigurationDetectorTests(SimpleTestCase):
    def test_known_nested_configurations(self):
        detector = module("components.configuration_detector")
        self.assertEqual(detector.configuration_type("pyproject.toml"), "Python project configuration")
        self.assertEqual(detector.configuration_type(".github/workflows/build.yml"), "GitHub Actions workflow")
        self.assertEqual(detector.configuration_type("infra/main.tf"), "Terraform configuration")

    def test_sensitive_files_are_never_read_by_detector(self):
        detector = module("components.configuration_detector")
        self.assertTrue(detector.is_sensitive_path("config/.env.production"))
        self.assertFalse(detector.is_sensitive_path(".env.example"))
        self.assertTrue(detector.is_sensitive_path(".npmrc"))
        self.assertTrue(detector.is_sensitive_path(".ssh/id_ed25519"))
