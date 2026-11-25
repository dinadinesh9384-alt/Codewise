from uuid import uuid4
from django.test import SimpleTestCase
from agent_foundation.contracts.evidence import EvidenceReference
from .support import module


class EntryPointDetectorTests(SimpleTestCase):
    def evidence(self, path, tool, start_line=None, end_line=None):
        return EvidenceReference(evidence_id="e" + str(start_line), evidence_type="file", snapshot_id=uuid4(), manifest_digest="a"*64,
            path=path, content_hash="b"*64, start_line=start_line, end_line=end_line)

    def test_candidates_and_disabled_mode(self):
        detect = module("components.entry_point_detector").detect_entry_points
        entries = detect(["manage.py", "src/main.ts", "other.txt"], {}, self.evidence)
        self.assertEqual({item.path for item in entries}, {"manage.py", "src/main.ts"})
        self.assertEqual(detect(["main.py"], {}, self.evidence, enabled=False), ())

    def test_package_declared_entrypoint(self):
        detect = module("components.entry_point_detector").detect_entry_points
        entries = detect(["package.json", "src/start.js"], {"package.json":b'{"main":"src/start.js"}'}, self.evidence)
        self.assertEqual(entries[0].classification, "confirmed_configuration")
