from uuid import uuid4
from django.test import SimpleTestCase
from agent_foundation.contracts.evidence import EvidenceReference, EvidenceType
from .support import module


class FrameworkDetectorTests(SimpleTestCase):
    def ref(self, path="requirements.txt"):
        return EvidenceReference(evidence_id="e", evidence_type=EvidenceType.FILE, snapshot_id=uuid4(),
            manifest_digest="a"*64, path=path, content_hash="b"*64)

    def test_framework_requires_evidence_and_can_be_disabled(self):
        detector = module("components.framework_detector").detect_frameworks
        dependency = type("Dep", (), {"name":"django", "evidence":self.ref()})
        self.assertEqual(detector([], [dependency()], lambda *args: self.ref())[0].name, "Django")
        self.assertEqual(detector([], [], lambda *args: self.ref()), ())
        self.assertEqual(detector([], [dependency()], lambda *args: self.ref(), include_frameworks=False), ())

    def test_infrastructure_signals(self):
        detector = module("components.framework_detector").detect_frameworks
        technologies = detector(["Dockerfile", ".github/workflows/ci.yml"], [], lambda path, tool: self.ref(path))
        self.assertEqual({item.name for item in technologies}, {"Docker", "GitHub Actions"})
