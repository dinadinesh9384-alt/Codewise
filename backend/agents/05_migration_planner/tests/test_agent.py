import hashlib
import uuid
from importlib import import_module

from django.test import SimpleTestCase

from agent_foundation.context import ExecutionContext
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from llm_gateway.pool_contracts import AGENT_POOL_MAP, PoolId

_service = import_module("agents.05_migration_planner.services.analysis_service")
_input = import_module("agents.05_migration_planner.schemas.input")
_definition = import_module("agents.05_migration_planner.definition")
_registry = import_module("agents.registry")


class Reader:
    def __init__(self, files):
        self.files = {path: value.encode() if isinstance(value, str) else value for path, value in files.items()}
        self.records = {path: {"path": path, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                        for path, data in self.files.items()}
    def list_file_metadata(self, context): return tuple(self.records.values())
    def list_paths(self, context): return tuple(self.files)
    def file_metadata(self, context, path): return self.records[path]
    def read_file(self, context, path, *, max_bytes=1_000_000): return self.files[path]


class FakeModel:
    def __init__(self, response=None, error=None):
        self.response = response or {"overview": "A bounded source snapshot was inventoried.",
            "database": "No database migration was evidenced.", "database_status": "not_detected",
            "data_migration_required": "unknown", "api": "No API contract was detected.", "api_status": "not_detected"}
        self.error, self.messages, self.calls = error, None, 0
    def with_max_output_tokens(self, value): self.max_output_tokens = value; return self
    def with_structured_output(self, schema):
        owner = self
        class Bound:
            def invoke(self, messages):
                owner.calls += 1; owner.messages = messages
                if owner.error: raise owner.error
                return owner.response
        return Bound()


class MigrationPlannerTests(SimpleTestCase):
    def setUp(self):
        self.ids = {key: uuid.uuid4() for key in ("execution", "workspace", "repository", "snapshot")}
        self.context = ExecutionContext.make(principal_id="test", workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha="a" * 40,
            manifest_digest="b" * 64, execution_id=self.ids["execution"], agent_id="migration_planner",
            permissions=frozenset({Permission.REPOSITORY_READ, Permission.ANALYSIS_RUN, Permission.LLM_INVOKE}),
            correlation_id="test", trace_id="test")
        self.request = _input.MigrationAnalysisInput(execution_id=self.ids["execution"], workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha="a" * 40,
            manifest_digest="b" * 64)

    def test_registration_requires_pool_b_gateway_reasoning(self):
        registry = AgentRegistry(); definition = _definition.register(registry, Reader({"pyproject.toml": "[project]\nname='x'\n"}))
        self.assertEqual(definition.contract.agent_id, "migration_planner")
        self.assertTrue(definition.contract.requires_llm)
        self.assertEqual(definition.contract.metadata["pool_id"], "pool_b")
        self.assertEqual(AGENT_POOL_MAP["migration_planner"], PoolId.POOL_B)

    def test_inventory_is_deterministic_snapshot_bound_and_does_not_send_source(self):
        files = {"pyproject.toml": "[project]\nname='sample'\nrequires-python='>=3.11'\ndependencies=['django==5.0.1']\n",
                 "app.py": "from .models import Item\n", "tests/test_app.py": "def test_x(): pass\n"}
        model = FakeModel()
        output = _service.analyze_repository(self.request, self.context, Reader(files), model)
        self.assertEqual(output.llm_status, "completed")
        self.assertEqual(output.plan_status, "incomplete")
        self.assertTrue(any(item.category == "framework" and item.name == "Django"
                            for item in output.inventory_facts))
        dependency = next(item for item in output.declared_dependencies if item.name == "django")
        self.assertEqual(dependency.exact_version, "5.0.1")
        self.assertNotIn("requires-python", model.messages[1].content)  # only parsed inventory is sent

    def test_missing_versions_and_secret_environment_files_are_not_claimed_or_disclosed(self):
        secret = "sk_live_never-send"
        files = {"requirements.txt": "django\n", ".env": f"API_KEY={secret}\n", ".env.example": "API_KEY=\n"}
        model = FakeModel()
        output = _service.analyze_repository(self.request, self.context, Reader(files), model)
        dep = next(item for item in output.declared_dependencies if item.name == "django")
        self.assertIsNone(dep.exact_version)
        self.assertNotIn(secret, model.messages[1].content)
        self.assertNotIn(secret, output.model_dump_json())

    def test_malformed_or_unavailable_model_keeps_deterministic_inventory(self):
        from agent_foundation.errors import LLMGatewayUnavailableError
        for model, status in ((FakeModel(response={"bad": "shape"}), "failed"),
                              (FakeModel(error=RuntimeError("provider failed")), "failed"),
                              (FakeModel(error=LLMGatewayUnavailableError("gateway disabled")), "unavailable")):
            output = _service.analyze_repository(self.request, self.context, Reader({"pyproject.toml": "[project]\nname='x'\n"}), model)
            self.assertEqual(output.llm_status, status)
            self.assertEqual(output.plan_status, "incomplete")
            self.assertTrue(output.inventory_facts)

    def test_cycle_unknown_evidence_and_unknown_finding_references_are_rejected(self):
        ids = {"evidence_ids": ["ev_0000000000000000"], "finding_ids": ["business_0000000000000000"]}
        base = {"overview": "x", "database": "unknown", "database_status": "unknown",
                "data_migration_required": "unknown", "api": "unknown", "api_status": "unknown"}
        # Strict structured output rejects malformed and unsupported references before producing a plan.
        response = {**base, "strategies": [{"key": "one", "name": "x", "description": "x", "advantages": ["x"],
            "tradeoffs": ["x"], "evidence_ids": ids["evidence_ids"], "target_assumption": "x"}]}
        output = _service.analyze_repository(self.request, self.context, Reader({"app.py": "x=1\n"}), FakeModel(response))
        self.assertEqual(output.llm_status, "failed")
        self.assertEqual(output.plan_status, "incomplete")

    def test_target_is_explicit_and_registry_entry_is_explicit(self):
        target = self.request.model_copy(update={"target_state": "Move to approved runtime"})
        response = {"overview": "Target is caller supplied.", "database": "unknown", "database_status": "unknown",
            "data_migration_required": "unknown", "api": "unknown", "api_status": "unknown"}
        output = _service.analyze_repository(target, self.context, Reader({"app.py": "x=1\n"}), FakeModel(response))
        self.assertEqual(output.target_state, target.target_state)
        self.assertFalse(any(item.status == "recommendation" for item in output.objectives))

    def test_evidence_backed_plan_is_proposed_but_never_approved_or_executed(self):
        reader = Reader({"app.py": "def run():\n    return 1\n"})
        baseline = _service.analyze_repository(self.request, self.context, reader,
            FakeModel(error=RuntimeError("offline")))
        evidence_id = baseline.evidence[0].evidence_id
        response = {"overview": "A small Python service is present.",
            "objectives": [{"statement": "Preserve behavior while updating the runtime.",
                "rationale": "The source is Python; no target runtime was supplied.",
                "evidence_ids": [evidence_id], "uncertainty": "Runtime compatibility is not established."}],
            "strategies": [{"key": "incremental", "name": "Incremental compatibility migration",
                "description": "Establish compatibility before changing runtime configuration.",
                "advantages": ["Limits change scope"], "tradeoffs": ["Requires staged validation"],
                "evidence_ids": [evidence_id], "target_assumption": "Target runtime remains unspecified.",
                "recommended": True}], "selected_strategy_key": "incremental",
            "phases": [{"key": "prepare", "order": 1, "title": "Prepare compatibility checks",
                "purpose": "Establish behavior checks before migration."}],
            "tasks": [{"key": "tests", "phase_key": "prepare", "sequence": 1,
                "title": "Add compatibility checks", "rationale": "Protect the observed entry point.",
                "priority": 2, "risk": "low", "complexity": "small", "evidence_ids": [evidence_id],
                "affected_files": ["app.py"], "outcome": "Behavior has a repeatable baseline.",
                "verification": ["Run the relevant unit tests."], "rollback": "Revert the test-only change."}],
            "database": "No database evidence was detected.", "database_status": "not_detected",
            "data_migration_required": "unknown", "api": "No API contract was detected.",
            "api_status": "not_detected"}
        output = _service.analyze_repository(self.request, self.context, reader, FakeModel(response))
        self.assertEqual(output.plan_status, "proposed")
        self.assertFalse(output.approved or output.executed)
        self.assertEqual(len(output.tasks), 1)
        self.assertEqual(output.phases[0].task_ids, (output.tasks[0].task_id,))
