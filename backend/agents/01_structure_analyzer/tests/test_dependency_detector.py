from django.test import SimpleTestCase
from .support import module


class DependencyDetectorTests(SimpleTestCase):
    def test_python_and_node_direct_dependencies(self):
        parser = module("components.dependency_detector").parse_dependencies
        py = parser("requirements.txt", b"Django>=5.0\nrequests~=2.31\n")
        self.assertEqual([(x.name, x.constraint) for x in py], [("Django", ">=5.0"), ("requests", "~=2.31")])
        js = parser("package.json", b'{"dependencies":{"react":"^18"},"devDependencies":{"vite":"5"}}')
        self.assertEqual([(x.name, x.group) for x in js], [("react", "default"), ("vite", "devDependencies")])

    def test_toml_xml_and_malformed_are_safe(self):
        parser = module("components.dependency_detector").parse_dependencies
        self.assertEqual(parser("pyproject.toml", b"[project]\ndependencies=['fastapi>=0.1']\n")[0].name, "fastapi")
        with self.assertRaises(ValueError):
            parser("pom.xml", b"<!DOCTYPE foo [<!ENTITY x SYSTEM 'file:///secret'>]><project>&x;</project>")
        with self.assertRaises(Exception): parser("pyproject.toml", b"not = [toml")

    def test_go_and_gradle_declarations(self):
        parser = module("components.dependency_detector").parse_dependencies
        self.assertEqual(parser("go.mod", b"module x\nrequire example.com/lib v1.2.3\n")[0].constraint, "v1.2.3")
        self.assertEqual(parser("build.gradle", b"implementation 'org.x:lib:1.0'\n")[0].name, "org.x:lib")

    def test_secrets_are_not_returned_as_constraints(self):
        parser = module("components.dependency_detector").parse_dependencies
        rows = parser("package.json", b'{"dependencies":{"safe-package":"ghp_1234567890123456789012345678901234"}}')
        self.assertEqual(rows[0].constraint, None)

    def test_malformed_manifest_shapes_are_rejected_without_execution(self):
        parser = module("components.dependency_detector").parse_dependencies
        with self.assertRaises(ValueError): parser("package.json", b'{"dependencies":"not-an-object"}')
