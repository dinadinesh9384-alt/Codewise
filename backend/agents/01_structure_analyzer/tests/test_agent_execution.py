import uuid
from django.test import SimpleTestCase
from pydantic import ValidationError

from .support import module


class AgentExecutionContractTests(SimpleTestCase):
    def test_input_requires_bound_snapshot_and_rejects_extra_fields(self):
        contract = module("schemas.input").StructureAnalysisInput
        with self.assertRaises(ValidationError): contract(execution_id=uuid.uuid4())
        with self.assertRaises(ValidationError): contract(execution_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            repository_id=uuid.uuid4(), snapshot_id=uuid.uuid4(), commit_sha="a"*40, manifest_digest="b"*64,
            secret="should fail")

    def test_analysis_limits_are_hard_bounded(self):
        options = module("schemas.input").AnalysisOptions
        with self.assertRaises(ValidationError): options(max_files=20_001)
        with self.assertRaises(ValidationError): options(max_total_content_bytes=50_000_000)
