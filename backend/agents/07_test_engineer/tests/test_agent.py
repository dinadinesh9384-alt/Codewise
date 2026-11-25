import hashlib
import builtins
import uuid
from importlib import import_module
from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic import ValidationError

from agent_foundation.context import ExecutionContext
from agent_foundation.errors import SnapshotUnavailableError
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from llm_gateway.pool_contracts import AGENT_POOL_MAP, PoolId
from orchestration.contracts import WorkflowRequest

_service = import_module("agents.07_test_engineer.services.analysis_service")
_schemas = import_module("agents.07_test_engineer.schemas.input")
_definition = import_module("agents.07_test_engineer.definition")
_inventory = import_module("agents.07_test_engineer.components.test_inventory")
_registry = import_module("agents.registry")


class Reader:
    def __init__(self, files):
        self.files = {p: value.encode() if isinstance(value, str) else value for p, value in files.items()}
        self.records = {p: {"path": p, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                        for p, raw in self.files.items()}
        self.reads = []

    def list_file_metadata(self, context): return tuple(self.records.values())
    def list_paths(self, context): return tuple(self.files)
    def file_metadata(self, context, path): return self.records[path]
    def read_file(self, context, path, *, max_bytes=1_000_000):
        self.reads.append(path)
        return self.files[path]


class FakeModel:
    def __init__(self, response): self.response, self.messages = response, None
    def with_max_output_tokens(self, value): self.max_tokens = value; return self
    def with_structured_output(self, schema):
        owner = self
        class Runnable:
            def invoke(self, messages): owner.messages = messages; return owner.response
        return Runnable()


class TestEngineerTests(SimpleTestCase):
    def setUp(self):
        self.ids = {name: uuid.uuid4() for name in ("execution", "workspace", "repository", "snapshot")}
        self.commit, self.digest = "a" * 40, "b" * 64
        self.context = ExecutionContext.make(principal_id="synthetic", workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha=self.commit,
            manifest_digest=self.digest, execution_id=self.ids["execution"], agent_id="test_engineer",
            permissions=frozenset({Permission.REPOSITORY_READ, Permission.ANALYSIS_RUN, Permission.LLM_INVOKE}),
            correlation_id="test", trace_id="test")
        agents = ("structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst",
                  "migration_planner", "patch_author")
        self.request = _schemas.TestEngineerInput(execution_id=self.ids["execution"],
            workspace_id=self.ids["workspace"], repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"],
            commit_sha=self.commit, manifest_digest=self.digest,
            upstream_statuses=tuple({"agent_id": a, "status": "skipped"} for a in agents))

    def _bound_upstream_results(self):
        path, raw = "app/service.py", b"def calculate():\n    return 1\n"
        digest = hashlib.sha256(raw).hexdigest()
        ev = {"evidence_id": "evidence_a1", "evidence_type": "file", "snapshot_id": self.ids["snapshot"],
            "manifest_digest": self.digest, "path": path, "start_line": 1, "end_line": 2,
            "content_hash": digest, "source_tool_id": "fixture", "direct": True}
        def identified(agent_id, **fields):
            return {"agent_id": agent_id, "repository_id": str(self.ids["repository"]),
                "snapshot_id": str(self.ids["snapshot"]), "commit_sha": self.commit,
                "manifest_digest": self.digest, **fields}
        a2_finding = {"finding_id": "business_find_1", "category": "business_logic", "title": "Workflow rule",
            "description": "A business rule applies.", "severity": "medium", "confidence": 0.8,
            "evidence": [ev]}
        a3_finding = {"finding_id": "quality_find_1", "category": "quality", "title": "Complex operation",
            "description": "A complex operation needs regression coverage.", "severity": "low", "confidence": 0.7,
            "evidence": [ev]}
        a4_finding = {"finding_id": "security_aaaaaaaaaaaaaaaa", "category": "security", "title": "Boundary risk",
            "description": "Validate authorization boundary.", "severity": "high", "confidence": 0.9,
            "evidence": [ev], "classification": "authorization", "remediation": "Verify access control."}
        migration_task = {"task_id": "migration_task_aaaaaaaaaaaaaaaa", "phase_id": "phase_aaaaaaaaaaaaaaaa",
            "sequence": 1, "title": "Preserve API compatibility", "rationale": "A migration changes the API.",
            "priority": 1, "risk": "high", "depends_on": [], "upstream_finding_ids": ["business_find_1"],
            "evidence_ids": ["evidence_a1"], "affected_files": [path], "expected_outcome": "Compatibility remains.",
            "verification_criteria": ["Old clients remain supported."], "breaking_changes": ["New field is optional."],
            "rollback_strategy": "Restore the previous contract."}
        patch_diff = "--- a/app/service.py\n+++ b/app/service.py\n@@ -1,2 +1,2 @@\n def calculate():\n-    return 1\n+    return 2\n"
        validate_diff = import_module("agents.06_patch_author.components.patch_validator").validate_unified_diff
        validated = validate_diff(path=path, diff=patch_diff, source=raw)
        patch = {"proposal_id": "patch_aaaaaaaaaaaaaaaa", "path": path,
            "original_content_hash": validated["original_content_hash"],
            "proposed_content_hash": validated["proposed_content_hash"], "unified_diff": patch_diff,
            "related_finding_ids": ["business_find_1"], "migration_task_ids": [migration_task["task_id"]],
            "evidence_ids": ["evidence_a1"], "rationale": "Correct the rule.",
            "expected_behavior": "Return the new value.", "risk": "medium",
            "compatibility_assessment": "The signature remains stable.", "required_tests": ["Test compatibility."],
            "uncertainty": "No runtime validation.", "validation_status": "validated", "application_status": "not_applied"}
        return {"business_logic_analyst": identified("business_logic_analyst", llm_status="completed",
                executive_overview="Order processing includes a cancellation workflow.", domain_summary="Orders",
                workflows=[{"workflow_id": "workflow_aaaaaaaaaaaaaaaa", "name": "Cancellation",
                    "description": "Cancels an order.", "steps": ["Authorize", "Cancel"], "evidence_ids": ["evidence_a1"]}],
                business_rules=[{"claim_id": "claim_aaaaaaaaaaaaaaaa", "title": "Cancellation policy",
            "description": "Only authorized orders can be cancelled.", "reasoning": "direct",
                    "evidence_ids": ["evidence_a1"]}],
                entities=[{"claim_id": "claim_bbbbbbbbbbbbbbbb", "title": "Order entity",
                    "description": "An order carries a cancellation state.", "reasoning": "inference",
                    "evidence_ids": ["evidence_a1"]}],
                relationships=[{"relationship_id": "relationship_aaaaaaaaaaaaaaaa", "source_entity": "Order",
                    "target_entity": "Cancellation", "relationship": "An order may have a cancellation event.",
                    "evidence_ids": ["evidence_a1"]}],
                findings=[a2_finding], evidence=[ev], uncertainties=["Cancellation side effects are unclear."]),
            "code_quality_analyst": identified("code_quality_analyst", llm_status="completed", coverage="partial",
                overview="Complex service operation.", findings=[a3_finding], evidence=[ev],
                complexity_metrics=[{"path": path, "lines_of_code": 2, "function_count": 1, "class_count": 0,
                    "average_cyclomatic_complexity": 3.0, "maximum_cyclomatic_complexity": 3,
                    "maximum_nesting_depth": 1, "missing_public_annotations": 1,
                    "evidence": [ev]}],
                recommendations=[{"recommendation_id": "recommendation_aaaaaaaaaaaaaaaa", "action": "Add boundary tests.",
                    "rationale": "Complex logic needs regression coverage.", "finding_ids": ["quality_find_1"],
                    "evidence_ids": ["evidence_a1"]}], uncertainties=["Only static metrics are available."]),
            "security_analyst": identified("security_analyst", llm_status="completed", coverage="partial",
                overview="Authorization boundary inspected.", scanned_files=1, findings=[a4_finding], evidence=[ev],
                assessments=[{"finding_id": a4_finding["finding_id"], "assessment": "potential_risk",
                    "rationale": "Static pattern needs review.", "confidence": 0.7, "uncertainty": "No runtime observation."}],
                recommendations=[{"recommendation_id": "security_recommendation_aaaaaaaaaaaaaaaa",
                    "action": "Test access denial.", "rationale": "Verify the boundary.",
                    "finding_ids": [a4_finding["finding_id"]], "evidence_ids": ["evidence_a1"]}],
                uncertainties=["Runtime enforcement was not tested."]),
            "migration_planner": identified("migration_planner", llm_status="completed", plan_status="proposed",
                current_state_assessment="The service has one API contract.", target_state="Optional field",
                target_state_uncertainty="Downstream consumers are unknown.", tasks=[migration_task], evidence=[ev],
                risks=[{"risk_id": "risk_aaaaaaaaaaaaaaaa", "title": "Client breakage", "description": "Clients may reject changes.",
                    "severity": "high", "likelihood": "medium", "mitigation": "Add compatibility checks.",
                    "upstream_finding_ids": ["business_find_1"], "evidence_ids": ["evidence_a1"], "uncertainty": "Client set unknown."}],
                declared_dependencies=[{"ecosystem": "python", "name": "requests", "version_spec": ">=2", "evidence_ids": ["evidence_a1"]}],
                internal_dependencies=[{"source_module": "api", "target_module": "service", "evidence_id": "evidence_a1"}],
                uncertainties=["Consumer inventory incomplete."]),
            "patch_author": identified("patch_author", status="awaiting_review", llm_status="completed",
                validation_status="passed", summary="One validated behavior proposal.", proposals=[patch],
                source_findings=[{"agent_id": "business_logic_analyst", "finding_id": "business_find_1",
                    "category": "business_logic", "confidence": 0.8, "title": "Workflow rule",
                    "description": "A business rule applies.", "severity": "medium", "evidence": [ev]}],
                evidence=[ev], uncertainties=["Proposal was not applied."]) }

    def test_registration_is_pool_c_and_has_no_execution_permissions(self):
        registry = AgentRegistry()
        definition = _definition.register(registry, Reader({}))
        self.assertEqual(definition.contract.agent_id, "test_engineer")
        self.assertEqual(AGENT_POOL_MAP["test_engineer"], PoolId.POOL_C)
        self.assertTrue(definition.contract.requires_llm)
        permissions = definition.contract.required_permissions
        self.assertNotIn(Permission.TESTS_EXECUTE, permissions)
        self.assertNotIn(Permission.SANDBOX_EXECUTE, permissions)
        self.assertNotIn(Permission.PATCH_APPLY, permissions)

    def test_inventory_is_static_and_coverage_is_only_measured_from_report(self):
        reader = Reader({"tests/test_rules.py": "import pytest\n@pytest.fixture\ndef item(): return 1\ndef test_empty():\n    assert item() == 1\n",
            "pyproject.toml": "[project.optional-dependencies]\ntest = ['pytest']\n",
            "coverage.json": '{"totals":{"covered_lines":3,"num_statements":4}}'})
        model = FakeModel({"summary": "The existing pytest suite has a measured coverage report.", "scenarios": [{
            "scenario_type": "boundary", "test_level": "unit", "title": "Check the empty input boundary",
            "priority": 2, "rationale": "An existing test file uses the pytest fixture and covers assertions.",
            "evidence_ids": ["test_evidence_" + _service._stable(self.ids["snapshot"], "tests/test_rules.py",
                reader.records["tests/test_rules.py"]["sha256"])], "preconditions": ["The test harness is available."],
            "inputs": ["empty input"], "steps": ["Call the behavior with empty input."],
            "expected_results": ["The documented empty-input behavior is returned."], "framework": "pytest",
            "uncertainty": "The implementation contract was not included in this synthetic snapshot."}]})
        output = _service.analyze_tests(self.request, self.context, reader, model)
        self.assertEqual(output.status, "partial")
        self.assertEqual(output.execution_status, "not_executed")
        self.assertEqual(output.test_files[0].test_case_count, 1)
        self.assertEqual(output.test_files[0].fixture_count, 1)
        self.assertEqual(output.coverage_observations[0].percent_covered, 75.0)
        self.assertEqual(output.coverage_observations[0].provenance, "snapshot_report_only")
        self.assertFalse(output.coverage_observations[0].independently_verified)
        self.assertCountEqual(reader.reads, ("tests/test_rules.py", "pyproject.toml", "coverage.json"))

    def test_unknown_finding_and_evidence_references_are_rejected(self):
        with self.assertRaises(ValidationError):
            _schemas.TestEngineerInput(execution_id=self.ids["execution"], workspace_id=self.ids["workspace"],
                repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha=self.commit,
                manifest_digest=self.digest, upstream_statuses=self.request.upstream_statuses,
                migration_tasks=({"task_id": "migration_task_0123456789abcdef", "title": "Task",
                    "rationale": "Because", "evidence_ids": ("missing",)},))

    def test_snapshot_manifest_rejects_fabricated_evidence_path(self):
        ref = _schemas.EvidenceReference(evidence_id="fabricated-evidence", evidence_type="file",
            snapshot_id=self.ids["snapshot"], manifest_digest=self.digest, path="missing/ghost.py",
            start_line=1, end_line=1, content_hash="c" * 64)
        request = self.request.model_copy(update={"evidence": (ref,)})
        with self.assertRaises(SnapshotUnavailableError):
            _service.analyze_tests(request, self.context, Reader({}),
                FakeModel({"summary": "No plan.", "scenarios": [], "uncertainties": []}))

    def test_patch_is_revalidated_against_snapshot_without_applying_it(self):
        validate_unified_diff = import_module("agents.06_patch_author.components.patch_validator").validate_unified_diff
        source_path = "app/service.py"
        source = b"def calculate():\n    return 1\n"
        diff = "--- a/app/service.py\n+++ b/app/service.py\n@@ -1,2 +1,2 @@\n def calculate():\n-    return 1\n+    return 2\n"
        validated = validate_unified_diff(path=source_path, diff=diff, source=source)
        ref = {"evidence_id": "source-evidence", "evidence_type": "file", "snapshot_id": self.ids["snapshot"],
            "manifest_digest": self.digest, "path": source_path, "start_line": 1, "end_line": 2,
            "content_hash": hashlib.sha256(source).hexdigest(), "source_tool_id": "test", "direct": True}
        evidence = _schemas.EvidenceReference.model_validate(ref)
        finding = {"agent_id": "business_logic_analyst", "finding_id": "finding-123", "category": "business_logic",
            "title": "Calculation behavior", "description": "The result is evidence-backed.", "severity": "medium",
            "confidence": 0.8, "evidence": (evidence,)}
        proposal = {"proposal_id": "patch_0123456789abcdef", "status": "awaiting_review", "validation_status": "passed",
            "path": source_path, "original_content_hash": validated["original_content_hash"],
            "proposed_content_hash": validated["proposed_content_hash"], "unified_diff": diff,
            "related_finding_ids": ("finding-123",), "evidence_ids": ("source-evidence",),
            "rationale": "Proposed behavior adjustment.", "expected_behavior": "Return the updated value.",
            "risk": "low", "compatibility_assessment": "Existing signature remains unchanged.",
            "required_tests": ("Verify expected return value.",), "uncertainty": "No runtime check performed."}
        statuses = tuple({"agent_id": a, "status": "completed" if a == "patch_author" else "skipped"}
                         for a in ("structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                                   "security_analyst", "migration_planner", "patch_author"))
        request = _schemas.TestEngineerInput(execution_id=self.ids["execution"], workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha=self.commit,
            manifest_digest=self.digest, upstream_statuses=statuses, findings=(finding,), patch_status="awaiting_review",
            patch_proposals=(proposal,), evidence=(evidence,))
        reader = Reader({source_path: source})
        output = _service.analyze_tests(request, self.context, reader,
            FakeModel({"summary": "The patch was checked in memory.", "scenarios": [], "uncertainties": []}))
        self.assertEqual(output.patch_analyses[0].analysis_status, "verified")
        self.assertEqual(output.patch_analyses[0].changed_symbols, ("calculate",))
        self.assertEqual(reader.files[source_path], source)
        self.assertEqual(output.execution_status, "not_executed")

    def test_language_agnostic_file_inventory_never_executes_test_modules(self):
        payload = "import os\nimport repo_payload\ndef test_payload():\n    open('marker', 'w').write('bad')\n    os.system('false')\n    eval('1 + 1')\n"
        reader = Reader({"tests/test_payload.py": payload})
        real_import = builtins.__import__
        def import_guard(name, *args, **kwargs):
            if name == "repo_payload":
                raise AssertionError("repository module import attempted")
            return real_import(name, *args, **kwargs)
        traps = (patch("builtins.open", side_effect=AssertionError("filesystem open called")),
                 patch("builtins.eval", side_effect=AssertionError("eval called")),
                 patch("builtins.__import__", side_effect=import_guard),
                 patch("os.system", side_effect=AssertionError("shell called")),
                 patch("subprocess.run", side_effect=AssertionError("subprocess called")),
                 patch("subprocess.Popen", side_effect=AssertionError("process started")))
        with traps[0], traps[1], traps[2], traps[3], traps[4], traps[5]:
            output = _service.analyze_tests(self.request, self.context, reader,
                FakeModel({"summary": "Static syntax was inventoried.", "scenarios": [], "uncertainties": []}))
        self.assertEqual(output.test_files[0].test_case_count, 1)
        self.assertEqual(reader.files["tests/test_payload.py"], payload.encode())
        self.assertFalse(hasattr(reader, "write_file"))

    def test_model_cannot_reference_an_unknown_finding(self):
        files = {"tests/test_rules.py": "def test_case():\n    assert True\n"}
        reader = Reader(files)
        evidence_id = "test_evidence_" + _service._stable(self.ids["snapshot"], "tests/test_rules.py",
            reader.records["tests/test_rules.py"]["sha256"])
        scenario = {"scenario_type": "regression", "test_level": "unit", "title": "Reject unknown relation",
            "priority": 1, "rationale": "Claimed finding must be known.", "related_finding_ids": ["fabricated"],
            "evidence_ids": [evidence_id], "preconditions": ["Fixture is loaded."], "inputs": ["value"],
            "steps": ["Exercise code."], "expected_results": ["Expected behavior."], "uncertainty": "Unknown."}
        output = _service.analyze_tests(self.request, self.context, reader,
            FakeModel({"summary": "Candidate plan.", "scenarios": [scenario]}))
        self.assertEqual(output.status, "rejected")
        self.assertEqual(output.scenarios, ())

    def test_binding_carries_typed_context_from_agents_2_to_6_into_the_model_prompt(self):
        import copy
        request = WorkflowRequest(execution_id=self.ids["execution"], workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha=self.commit,
            manifest_digest=self.digest, correlation_id="integration", trace_id="integration")
        results = self._bound_upstream_results()
        lifecycle = {agent: "completed" for agent in ("structure_analyzer", "business_logic_analyst",
            "code_quality_analyst", "security_analyst", "migration_planner", "patch_author")}
        binding = _registry.test_engineer_binding(lambda *args: self.context)
        bound = binding.input_factory_with_state(request, results, lifecycle)
        self.assertEqual(bound.business_context.workflows[0].name, "Cancellation")
        self.assertEqual(bound.business_context.business_rules[0].title, "Cancellation policy")
        self.assertEqual(bound.business_context.entities[0].title, "Order entity")
        self.assertEqual(bound.business_context.relationships[0].relationship_id, "relationship_aaaaaaaaaaaaaaaa")
        self.assertEqual({row.finding_id for row in bound.findings}, {
            "business_find_1", "quality_find_1", "security_aaaaaaaaaaaaaaaa"})
        self.assertEqual(bound.quality_context.metrics[0].maximum_cyclomatic_complexity, 3)
        self.assertEqual(bound.quality_context.metrics[0].path, "app/service.py")
        self.assertEqual(bound.quality_context.recommendations[0].finding_ids, ("quality_find_1",))
        self.assertEqual(bound.security_context.assessments[0].assessment, "potential_risk")
        self.assertEqual(bound.security_context.recommendations[0].action, "Test access denial.")
        self.assertEqual(bound.migration_tasks[0].priority, 1)
        self.assertEqual(bound.migration_tasks[0].depends_on, ())
        self.assertEqual(bound.migration_context.risks[0].title, "Client breakage")
        self.assertEqual(bound.patch_proposals[0].proposal_id, "patch_aaaaaaaaaaaaaaaa")
        self.assertEqual(bound.patch_context.source_findings[0].finding_id, "business_find_1")
        self.assertEqual(bound.patch_proposals[0].related_finding_ids, ("business_find_1",))
        self.assertEqual(bound.patch_context.snapshot_id, self.ids["snapshot"])

        reader = Reader({"app/service.py": b"def calculate():\n    return 1\n",
            "tests/test_service.py": "def test_calculate():\n    assert True\n"})
        execution_context = self.context.model_copy(update={"execution_id": bound.execution_id})
        model_a = FakeModel({"summary": "Bound evidence informs a proposed test plan.", "scenarios": [], "uncertainties": []})
        _service.analyze_tests(bound, execution_context, reader, model_a)
        changed_results = copy.deepcopy(results)
        changed_results["business_logic_analyst"]["executive_overview"] = "Inventory authorization gates a refund workflow."
        changed = binding.input_factory_with_state(request, changed_results, lifecycle)
        model_b = FakeModel({"summary": "Bound evidence informs a proposed test plan.", "scenarios": [], "uncertainties": []})
        _service.analyze_tests(changed, execution_context, reader, model_b)
        prompt_a = "\n".join(str(message.content) for message in model_a.messages)
        prompt_b = "\n".join(str(message.content) for message in model_b.messages)
        self.assertIn("cancellation workflow", prompt_a)
        self.assertIn("Complex operation", prompt_a)
        self.assertIn("Boundary risk", prompt_a)
        self.assertIn("Complex service operation", prompt_a)
        self.assertIn("Test access denial.", prompt_a)
        self.assertIn("Preserve API compatibility", prompt_a)
        self.assertIn("One validated behavior proposal.", prompt_a)
        self.assertIn("authorization gates a refund workflow", prompt_b)
        self.assertNotEqual(prompt_a, prompt_b)

    def test_completed_lifecycle_with_unavailable_llm_is_not_reported_as_successful_analysis(self):
        request = WorkflowRequest(execution_id=self.ids["execution"], workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha=self.commit,
            manifest_digest=self.digest, correlation_id="status", trace_id="status")
        results = self._bound_upstream_results()
        results["business_logic_analyst"]["llm_status"] = "unavailable"
        statuses = {agent: "completed" for agent in ("structure_analyzer", "business_logic_analyst",
            "code_quality_analyst", "security_analyst", "migration_planner", "patch_author")}
        binding = _registry.test_engineer_binding(lambda *args: self.context)
        bound = binding.input_factory_with_state(request, results, statuses)
        self.assertEqual(bound.upstream_statuses[1].status, "completed")
        self.assertEqual(bound.upstream_statuses[1].execution_status, "completed")
        self.assertEqual(bound.upstream_statuses[1].llm_status, "unavailable")
        reader = Reader({"app/service.py": b"def calculate():\n    return 1\n",
            "tests/test_service.py": "def test_calculate():\n    assert True\n"})
        execution_context = self.context.model_copy(update={"execution_id": bound.execution_id})
        model = FakeModel({"summary": "Test context is partial because business reasoning is unavailable.",
            "scenarios": [], "uncertainties": []})
        output = _service.analyze_tests(bound, execution_context, reader, model)
        self.assertIn("business_logic_analyst", output.unavailable_upstream_agents)
        self.assertIn("business_logic_analyst", output.missing_upstream_agents)
        self.assertIn("business_logic_analyst", output.partial_upstream_agents)
        prompt = "\n".join(str(message.content) for message in model.messages)
        self.assertIn('"llm_status":"unavailable"', prompt)
        self.assertIn("never infer no tests are needed", prompt)

    def test_model_cannot_claim_unexecuted_tests_passed_or_invent_coverage(self):
        reader = Reader({"tests/test_rules.py": "def test_case():\n    assert True\n"})
        for claim in ("All tests passed.", "This test passes.", "Measured coverage is 99%."):
            with self.subTest(claim=claim):
                output = _service.analyze_tests(self.request, self.context, reader,
                    FakeModel({"summary": claim, "scenarios": [], "uncertainties": []}))
                self.assertEqual(output.status, "rejected")
                self.assertEqual(output.execution_status, "not_executed")

    def test_skipped_failed_missing_and_partial_statuses_remain_distinct(self):
        statuses = list(self.request.upstream_statuses)
        statuses[0] = statuses[0].model_copy(update={"selected": False, "status": "skipped", "execution_status": "skipped"})
        statuses[1] = statuses[1].model_copy(update={"selected": True, "status": "failed", "execution_status": "failed"})
        statuses[2] = statuses[2].model_copy(update={"selected": True, "status": "unavailable", "execution_status": "missing_output"})
        statuses[3] = statuses[3].model_copy(update={"selected": True, "status": "completed", "execution_status": "completed",
            "output_present": True, "analysis_status": "partial", "llm_status": "completed"})
        request = self.request.model_copy(update={"upstream_statuses": tuple(statuses)})
        reader = Reader({"tests/test_rules.py": "def test_case():\n    assert True\n"})
        output = _service.analyze_tests(request, self.context, reader,
            FakeModel({"summary": "Partial context remains uncertain.", "scenarios": [], "uncertainties": []}))
        self.assertIn("structure_analyzer", output.skipped_upstream_agents)
        self.assertIn("business_logic_analyst", output.failed_upstream_agents)
        self.assertIn("code_quality_analyst", output.unavailable_upstream_agents)
        self.assertIn("security_analyst", output.partial_upstream_agents)

    def test_model_cannot_claim_a_different_coverage_number_than_report(self):
        reader = Reader({"tests/test_rules.py": "def test_case():\n    assert True\n",
            "coverage.json": '{"totals":{"covered_lines":3,"num_statements":4}}'})
        output = _service.analyze_tests(self.request, self.context, reader,
            FakeModel({"summary": "The report shows 99% coverage.", "scenarios": [], "uncertainties": []}))
        self.assertEqual(output.status, "rejected")

