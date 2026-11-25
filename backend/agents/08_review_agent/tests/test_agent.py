import builtins
import hashlib
import uuid
from importlib import import_module
from unittest.mock import patch
from uuid import uuid5

from django.test import SimpleTestCase
from pydantic import ValidationError

from agent_foundation.context import ExecutionContext
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from llm_gateway.pool_contracts import AGENT_POOL_MAP, PoolId
from orchestration.contracts import WorkflowRequest

_input = import_module("agents.08_review_agent.schemas.input")
_service = import_module("agents.08_review_agent.services.review_service")
_definition = import_module("agents.08_review_agent.definition")


class Reader:
    def __init__(self, files):
        self.files = {path: value if isinstance(value, bytes) else value.encode() for path, value in files.items()}
        self.reads = []
    def read_file(self, context, path, *, max_bytes=1_000_000):
        self.reads.append(path)
        return self.files[path]


class FakeModel:
    def __init__(self, result): self.result, self.messages, self.schema = result, None, None
    def with_max_output_tokens(self, value): self.max_tokens = value; return self
    def with_structured_output(self, schema):
        self.schema = schema
        owner = self
        class Runnable:
            def invoke(self, messages): owner.messages = messages; return owner.result
        return Runnable()


class ReviewAgentTests(SimpleTestCase):
    def setUp(self):
        self.ids = {key: uuid.uuid4() for key in ("execution", "workspace", "repository", "snapshot")}
        self.commit, self.digest = "a" * 40, "b" * 64
        self.context = ExecutionContext.make(principal_id="review-test", workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha=self.commit,
            manifest_digest=self.digest, execution_id=self.ids["execution"], agent_id="review_agent",
            permissions=frozenset({Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ, Permission.LLM_INVOKE}),
            correlation_id="review", trace_id="review")
        self.raw = b"def calculate():\n    return 1\n"
        self.evidence_id = "evidence_review"
        from agent_foundation.contracts.evidence import EvidenceReference
        self.evidence = EvidenceReference(evidence_id=self.evidence_id, evidence_type="file",
            snapshot_id=self.ids["snapshot"], manifest_digest=self.digest, path="app/service.py",
            start_line=1, end_line=2, content_hash=hashlib.sha256(self.raw).hexdigest(), source_tool_id="fixture")

    def make_input(self, *, complete=True, scenario=False, patch=None):
        from agent_foundation.contracts.evidence import EvidenceReference
        names = ("structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst",
                 "migration_planner", "patch_author", "test_engineer")
        statuses = []
        for name in names:
            is_required = name in {"structure_analyzer", "business_logic_analyst"}
            done = complete or is_required
            statuses.append({"agent_id": name, "status": "completed" if done else "skipped",
                "selected": done, "output_present": done,
                "llm_status": "not_attempted" if name == "structure_analyzer" else ("completed" if done else None),
                "analysis_status": "complete" if done else None})
        findings = ({"agent_id": "business_logic_analyst", "finding_id": "finding_business_01",
            "category": "business_logic", "title": "Cancellation authorization", "description": "Authorization must be checked.",
            "severity": "high", "confidence": 0.9, "evidence_ids": (self.evidence_id,)},)
        scenarios = ({"scenario_id": "test_scenario_0123456789abcdef", "scenario_type": "security",
            "test_level": "unit", "title": "Reject unauthorized cancellation", "priority": 1,
            "rationale": "A security finding identifies an authorization boundary.",
            "preconditions": ("The request is unauthenticated.",), "inputs": ("unauthorized request",),
            "steps": ("Attempt cancellation.",),
            "related_finding_ids": ("finding_business_01",), "migration_task_ids": (), "patch_proposal_ids": (),
            "evidence_ids": (self.evidence_id,), "expected_results": ("Access is denied.",), "framework": "pytest",
            "execution_status": "not_executed"},) if scenario else ()
        return _input.ReviewAgentInput(execution_id=self.ids["execution"], workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha=self.commit,
            manifest_digest=self.digest, upstream_statuses=tuple(statuses),
            repository={"file_count": 1, "directory_count": 1, "files_inspected": 1, "files_skipped": 0,
                "completion_status": "complete", "languages": ("Python",), "technologies": (), "entry_points": (),
                "configurations": (), "dependencies": (), "limitations": ()},
            findings=findings, test_scenarios=scenarios, patch_proposals=(patch,) if patch else (), evidence=(self.evidence,))

    def test_registration_is_pool_c_and_has_only_read_and_reasoning_permissions(self):
        registry = AgentRegistry()
        definition = _definition.register(registry, Reader({}))
        self.assertEqual(definition.contract.agent_id, "review_agent")
        self.assertEqual(AGENT_POOL_MAP["review_agent"], PoolId.POOL_C)
        self.assertTrue(definition.contract.requires_llm)
        self.assertFalse({Permission.TESTS_EXECUTE, Permission.SANDBOX_EXECUTE, Permission.PATCH_APPLY,
                          Permission.GITHUB_PUBLISH} & definition.contract.required_permissions)

    def test_structured_review_checks_evidence_and_never_approves(self):
        reader = Reader({"app/service.py": self.raw})
        model = FakeModel({"executive_summary": "Review the authorization evidence before a human decision.",
            "summary_upstream_ids": ["finding_business_01"], "summary_evidence_ids": [self.evidence_id],
            "assessments": [{"assessment_type": "model_assessment", "topic": "security",
                "statement": "The authorization boundary deserves focused review.",
                "upstream_ids": ["finding_business_01"], "evidence_ids": [self.evidence_id],
                "uncertainty": "Static evidence cannot show runtime enforcement."}],
            "conflicts": [], "uncertainties": []})
        output = _service.review_analysis(self.make_input(), self.context, reader, model)
        self.assertEqual(output.review_status, "ready_with_warnings")
        self.assertEqual(output.evidence_validation[0].validation_status, "hash_verified")
        self.assertEqual(output.evidence_supported_findings, ("finding_business_01",))
        self.assertEqual(output.human_review_recommendation, "required")
        self.assertFalse(output.approved_by_human)
        self.assertEqual(model.schema.__name__, "_Interpretation")
        self.assertIn("authorization", model.messages[1].content)

    def test_scenarios_remain_not_executed_and_missing_optional_agents_are_incomplete(self):
        reader = Reader({"app/service.py": self.raw})
        model = FakeModel({"executive_summary": "Optional analysis was not supplied.",
            "summary_upstream_ids": ["code_quality_analyst"], "assessments": [],
            "conflicts": [], "uncertainties": []})
        request = self.make_input(complete=False, scenario=True)
        output = _service.review_analysis(request, self.context, reader, model)
        self.assertEqual(output.review_status, "incomplete")
        self.assertEqual(output.test_scenarios_not_executed, ("test_scenario_0123456789abcdef",))
        self.assertIn("code_quality_analyst", output.skipped_agents)

    def test_unknown_finding_and_evidence_references_are_rejected_before_model_call(self):
        request = self.make_input(scenario=True)
        with self.assertRaises(ValidationError):
            _input.ReviewAgentInput.model_validate({**request.model_dump(), "test_scenarios": (
                {**request.test_scenarios[0].model_dump(), "related_finding_ids": ("fabricated",)},)})
        with self.assertRaises(ValidationError):
            _input.ReviewAgentInput.model_validate({**request.model_dump(), "findings": (
                {**request.findings[0].model_dump(), "evidence_ids": ("unknown-evidence",)},)})
        bad_task = {"task_id": "migration_task_aaaaaaaaaaaaaaaa", "title": "Task", "rationale": "Invalid link.",
            "priority": 1, "risk": "low", "depends_on": ("migration_task_bbbbbbbbbbbbbbbb",),
            "finding_ids": (), "evidence_ids": (self.evidence_id,), "expected_outcome": "Outcome."}
        with self.assertRaises(ValidationError):
            _input.ReviewAgentInput.model_validate({**request.model_dump(), "migration_tasks": (bad_task,)})
        bad_patch = {"proposal_id": "patch_aaaaaaaaaaaaaaaa", "path": "app/service.py",
            "validation_status": "passed", "proposal_status": "awaiting_review",
            "original_content_hash": self.evidence.content_hash, "proposed_content_hash": "c" * 64,
            "diff": "--- a/app/service.py\n+++ b/app/service.py\n@@ -1 +1 @@\n-a\n+b\n",
            "source_finding_ids": ("unknown-finding",), "evidence_ids": (self.evidence_id,),
            "summary": "Change", "rationale": "Rationale", "risk": "low"}
        with self.assertRaises(ValidationError):
            _input.ReviewAgentInput.model_validate({**request.model_dump(), "patch_proposals": (bad_patch,)})
        bad_scenario = {"scenario_id": "test_scenario_0123456789abcdef", "scenario_type": "regression",
            "test_level": "unit", "title": "Check patch", "priority": 1, "rationale": "The patch is relevant.",
            "steps": ("Apply a test case.",), "expected_results": ("Expected behavior.",),
            "evidence_ids": (self.evidence_id,), "patch_proposal_ids": ("patch_bbbbbbbbbbbbbbbb",)}
        with self.assertRaises(ValidationError):
            _input.ReviewAgentInput.model_validate({**request.model_dump(), "test_scenarios": (bad_scenario,)})

    def test_hash_mismatch_makes_finding_weak_and_requires_changes(self):
        bad_ref = self.evidence.model_copy(update={"content_hash": "c" * 64})
        request = self.make_input().model_copy(update={"evidence": (bad_ref,)})
        output = _service.review_analysis(request, self.context, Reader({"app/service.py": self.raw}),
            FakeModel({"executive_summary": "Evidence needs review.", "summary_upstream_ids": ["business_logic_analyst"],
                "assessments": [], "conflicts": [], "uncertainties": []}))
        self.assertEqual(output.review_status, "changes_required")
        self.assertEqual(output.weakly_evidenced_findings, ("finding_business_01",))
        self.assertEqual(output.evidence_validation[0].validation_status, "hash_mismatch")

    def test_model_cannot_claim_tests_passed_or_invent_ids(self):
        reader = Reader({"app/service.py": self.raw})
        for response in (
            {"executive_summary": "All tests passed.", "summary_upstream_ids": ["test_engineer"], "assessments": [], "conflicts": [], "uncertainties": []},
            {"executive_summary": "Review is incomplete.", "summary_upstream_ids": ["business_logic_analyst"], "assessments": [{"assessment_type": "model_assessment",
                "topic": "testing", "statement": "Unknown scenario is sufficient.", "upstream_ids": ["fake-id"],
                "evidence_ids": [], "uncertainty": "The reference is unavailable."}], "conflicts": [], "uncertainties": []},
        ):
            output = _service.review_analysis(self.make_input(scenario=True), self.context, reader, FakeModel(response))
            self.assertEqual(output.llm_status, "failed")
            self.assertEqual(output.assessments, ())
            self.assertIn("unsupported_model_claim_rejected", output.validation_errors)

    def test_unavailable_mandatory_agent_blocks_without_invoking_model(self):
        request = self.make_input()
        statuses = list(request.upstream_statuses)
        statuses[1] = statuses[1].model_copy(update={"status": "failed", "output_present": False})
        request = request.model_copy(update={"upstream_statuses": tuple(statuses)})
        model = FakeModel({})
        output = _service.review_analysis(request, self.context, Reader({"app/service.py": self.raw}), model)
        self.assertEqual(output.review_status, "blocked")
        self.assertEqual(output.llm_status, "not_attempted")
        self.assertIsNone(model.messages)

    def test_failed_missing_and_partial_upstream_states_are_distinct(self):
        reader = Reader({"app/service.py": self.raw})
        response = {"executive_summary": "The review has incomplete upstream analysis.",
            "summary_upstream_ids": ["business_logic_analyst"], "assessments": [],
            "conflicts": [], "uncertainties": []}
        request = self.make_input(complete=False)
        statuses = list(request.upstream_statuses)
        statuses[2] = statuses[2].model_copy(update={"selected": True, "status": "failed", "output_present": False})
        statuses[3] = statuses[3].model_copy(update={"selected": True, "status": "completed", "output_present": False,
            "analysis_status": "missing_output", "validation_status": None})
        statuses[4] = statuses[4].model_copy(update={"selected": True, "status": "completed", "output_present": True,
            "analysis_status": "partial", "llm_status": "completed"})
        request = _input.ReviewAgentInput.model_validate({**request.model_dump(), "upstream_statuses": statuses})
        output = _service.review_analysis(request, self.context, reader, FakeModel(response))
        self.assertEqual(output.review_status, "incomplete")
        self.assertIn("code_quality_analyst", output.failed_agents)
        self.assertIn("security_analyst", output.unavailable_agents)
        self.assertIn("migration_planner", output.partial_agents)
        self.assertIn("patch_author", output.skipped_agents)

    def test_patch_diff_is_revalidated_and_invalid_diff_requires_changes(self):
        validate = import_module("agents.06_patch_author.components.patch_validator").validate_unified_diff
        diff = "--- a/app/service.py\n+++ b/app/service.py\n@@ -1,2 +1,2 @@\n def calculate():\n-    return 1\n+    return 2\n"
        hashes = validate(path="app/service.py", diff=diff, source=self.raw)
        patch_data = {"proposal_id": "patch_aaaaaaaaaaaaaaaa", "path": "app/service.py",
            "validation_status": "passed", "proposal_status": "awaiting_review",
            "original_content_hash": hashes["original_content_hash"],
            "proposed_content_hash": hashes["proposed_content_hash"], "diff": diff,
            "source_finding_ids": ("finding_business_01",), "evidence_ids": (self.evidence_id,),
            "summary": "Proposed change.", "rationale": "It addresses the cited behavior.",
            "risk": "low"}
        request = self.make_input(patch=patch_data)
        model = FakeModel({"executive_summary": "Proposal validation is independent of approval.",
            "summary_upstream_ids": ["patch_author"],
            "assessments": [], "conflicts": [], "uncertainties": []})
        result = _service.review_analysis(request, self.context, Reader({"app/service.py": self.raw}), model)
        self.assertEqual(result.patch_reviews[0].validation_status, "verified_in_memory")
        self.assertFalse(result.patch_reviews[0].applied)
        bad_patch = {**patch_data, "diff": diff.replace("return 1", "return 99", 1)}
        bad_request = self.make_input(patch=bad_patch)
        bad = _service.review_analysis(bad_request, self.context, Reader({"app/service.py": self.raw}),
            FakeModel({"executive_summary": "The proposed change requires corrective review.",
                "summary_upstream_ids": ["patch_author"],
                "assessments": [], "conflicts": [], "uncertainties": []}))
        self.assertEqual(bad.review_status, "changes_required")
        self.assertEqual(bad.patch_reviews[0].validation_status, "failed")

    def test_migration_dependency_cycles_are_reported_deterministically(self):
        from agent_foundation.contracts.evidence import EvidenceReference
        task_a, task_b = "migration_task_aaaaaaaaaaaaaaaa", "migration_task_bbbbbbbbbbbbbbbb"
        tasks = ({"task_id": task_a, "title": "A", "rationale": "Cycle test", "priority": 1,
            "risk": "low", "depends_on": (task_b,), "finding_ids": (), "evidence_ids": (self.evidence_id,),
            "expected_outcome": "A"}, {"task_id": task_b, "title": "B", "rationale": "Cycle test", "priority": 2,
            "risk": "low", "depends_on": (task_a,), "finding_ids": (), "evidence_ids": (self.evidence_id,),
            "expected_outcome": "B"})
        request = self.make_input().model_copy(update={"migration_tasks": tuple(
            _input.ReviewTask.model_validate(row) for row in tasks)})
        output = _service.review_analysis(request, self.context, Reader({"app/service.py": self.raw}),
            FakeModel({"executive_summary": "A migration dependency cycle requires correction.",
                "summary_upstream_ids": ["migration_planner"],
                "assessments": [], "conflicts": [], "uncertainties": []}))
        self.assertEqual(output.review_status, "changes_required")
        self.assertIn("migration_dependency_cycle", output.validation_errors)

    def test_forbidden_execution_and_write_operations_are_trapped(self):
        reader = Reader({"app/service.py": self.raw})
        model = FakeModel({"executive_summary": "Static evidence requires human review.",
            "summary_upstream_ids": ["business_logic_analyst"], "assessments": [],
            "conflicts": [], "uncertainties": []})
        trap = AssertionError("forbidden repository or process operation invoked")
        with patch("builtins.open", side_effect=trap), patch("builtins.eval", side_effect=trap), \
             patch("os.system", side_effect=trap), patch("subprocess.run", side_effect=trap), \
             patch("subprocess.Popen", side_effect=trap), patch("pathlib.Path.write_text", side_effect=trap), \
             patch("pathlib.Path.write_bytes", side_effect=trap), patch("pathlib.Path.unlink", side_effect=trap), \
             patch("os.remove", side_effect=trap), patch("os.replace", side_effect=trap), patch("os.rename", side_effect=trap):
            output = _service.review_analysis(self.make_input(), self.context, reader, model)
        self.assertFalse(hasattr(reader, "write_file"))
        self.assertEqual(output.review_status, "ready_for_human_review")

    def test_binding_consumes_schema_validated_outputs_from_agents_1_through_7(self):
        from agent_foundation.contracts.findings import Finding
        def module(name): return import_module(name)
        def identity(agent):
            return {"execution_id": uuid5(self.ids["execution"], agent), "repository_id": self.ids["repository"],
                "snapshot_id": self.ids["snapshot"], "commit_sha": self.commit, "manifest_digest": self.digest,
                "agent_version": "fixture"}
        test_raw = b"def test_calculate():\n    assert True\n"
        from agent_foundation.contracts.evidence import EvidenceReference
        test_ev = EvidenceReference(evidence_id="evidence_test", evidence_type="file", snapshot_id=self.ids["snapshot"],
            manifest_digest=self.digest, path="tests/test_service.py", start_line=1, end_line=2,
            content_hash=hashlib.sha256(test_raw).hexdigest(), source_tool_id="fixture")
        repo_schemas = module("agents.01_structure_analyzer.schemas.repository")
        structure = module("agents.01_structure_analyzer.schemas.output").StructureAnalysisOutput(
            **identity("structure_analyzer"), contract_version="structure-analysis.output.v1", agent_id="structure_analyzer",
            options={}, overview={"file_count": 2, "directory_count": 2, "total_bytes": len(self.raw)+len(test_raw),
                "maximum_directory_depth": 2, "languages": ({"language": "python", "file_count": 2, "bytes": 100,
                    "detection_methods": ("extension",), "certainty": "high", "evidence": (self.evidence, test_ev)},)},
            tree=(), technologies=(), configurations=(), dependency_manifests=(), entry_points=(), observations=(),
            quality={"completion_status": "complete", "files_inspected": 2, "files_skipped": 0,
                "content_bytes_inspected": len(self.raw)+len(test_raw)}, findings=(), evidence=(self.evidence, test_ev))
        business = module("agents.02_business_logic_analyst.schemas.output")
        business_finding = Finding(finding_id="finding_business_01", category="business_logic",
            title="Authorization boundary", description="Cancellation requires authorization.", severity="high",
            confidence=0.9, evidence=(self.evidence,))
        business_output = business.BusinessAnalysisOutput(**identity("business_logic_analyst"),
            executive_overview="Orders support authorized cancellation.", domain_summary="Order processing.",
            workflows=({"workflow_id": "workflow_aaaaaaaaaaaaaaaa", "name": "Cancellation",
                "description": "Authorize then cancel an order.", "steps": ("authorize", "cancel"),
                "evidence_ids": (self.evidence_id,), "confidence": 0.9},),
            business_rules=({"claim_id": "claim_aaaaaaaaaaaaaaaa", "title": "Cancellation authorization",
                "description": "Only authorized requests cancel orders.", "reasoning": "direct",
                "evidence_ids": (self.evidence_id,)},), findings=(business_finding,), evidence=(self.evidence,),
            coverage="complete", llm_status="completed")
        quality = module("agents.03_code_quality_analyst.schemas.output")
        quality_finding = Finding(finding_id="quality_finding_01", category="quality", title="Complex service",
            description="Review branching behavior.", severity="medium", confidence=0.8, evidence=(self.evidence,))
        quality_output = quality.CodeQualityOutput(**identity("code_quality_analyst"), coverage="complete",
            overview="A service function has branching complexity.", analyzed_files=1,
            complexity_metrics=({"path": "app/service.py", "lines_of_code": 2, "function_count": 1,
                "class_count": 0, "average_cyclomatic_complexity": 2.0, "maximum_cyclomatic_complexity": 2,
                "maximum_nesting_depth": 1, "missing_public_annotations": 0, "evidence": (self.evidence,)},),
            findings=(quality_finding,), evidence=(self.evidence,), llm_status="completed")
        security = module("agents.04_security_analyst.schemas.output")
        security_finding = security.SecurityFinding(finding_id="security_aaaaaaaaaaaaaaaa", category="security",
            title="Authorization guard", description="Check the authorization boundary.", severity="high",
            confidence=0.9, evidence=(self.evidence,), classification="authorization", remediation="Test denied access.")
        security_output = security.SecurityAnalysisOutput(**identity("security_analyst"), coverage="complete",
            overview="Authorization control requires review.", findings=(security_finding,),
            assessments=({"finding_id": security_finding.finding_id, "assessment": "potential_risk",
                "rationale": "Static evidence cannot show runtime enforcement.", "confidence": 0.7,
                "uncertainty": "Runtime behavior is unknown."},), evidence=(self.evidence,), llm_status="completed")
        migration = module("agents.05_migration_planner.schemas.output")
        task_id, phase_id = "migration_task_aaaaaaaaaaaaaaaa", "phase_aaaaaaaaaaaaaaaa"
        migration_output = migration.MigrationPlanOutput(**identity("migration_planner"), plan_status="proposed",
            coverage="complete", current_state_assessment="Current API is synchronous.", target_state="Preserve compatibility.",
            phases=({"phase_id": phase_id, "order": 1, "title": "Compatibility", "purpose": "Preserve callers.",
                "task_ids": (task_id,)},),
            tasks=({"task_id": task_id, "phase_id": phase_id, "sequence": 1, "title": "Preserve API compatibility",
                "rationale": "Existing clients depend on the response.", "priority": 1, "risk": "medium",
                "implementation_complexity": "small", "depends_on": (), "upstream_finding_ids": (),
                "evidence_ids": (self.evidence_id,), "affected_files": ("app/service.py",),
                "expected_outcome": "Existing clients continue to work.", "verification_criteria": ("Test old clients.",),
                "rollback_strategy": "Restore prior response contract."},), evidence=(self.evidence,), llm_status="completed")
        patch_mod = module("agents.06_patch_author.schemas.output")
        diff = "--- a/app/service.py\n+++ b/app/service.py\n@@ -1,2 +1,2 @@\n def calculate():\n-    return 1\n+    return 2\n"
        validator = module("agents.06_patch_author.components.patch_validator").validate_unified_diff
        validated = validator(path="app/service.py", diff=diff, source=self.raw)
        patch_id = "patch_aaaaaaaaaaaaaaaa"
        source_finding = module("agents.06_patch_author.schemas.input").PatchFinding(agent_id="business_logic_analyst",
            finding_id=business_finding.finding_id, category="business_logic", title=business_finding.title,
            description=business_finding.description, severity="high", confidence=0.9, evidence=(self.evidence,))
        patch_output = patch_mod.PatchAuthorOutput(**identity("patch_author"), status="awaiting_review",
            llm_status="completed", validation_status="passed", summary="Validated compatibility proposal.",
            source_findings=(source_finding,), migration_tasks=({"task_id": task_id, "title": "Preserve API compatibility",
                "rationale": "Keep clients stable.", "evidence_ids": (self.evidence_id,),
                "affected_files": ("app/service.py",)},),
            proposals=({"proposal_id": patch_id, "path": "app/service.py",
                "original_content_hash": validated["original_content_hash"], "proposed_content_hash": validated["proposed_content_hash"],
                "unified_diff": diff, "related_finding_ids": (business_finding.finding_id,),
                "migration_task_ids": (task_id,), "evidence_ids": (self.evidence_id,), "rationale": "Correct response.",
                "expected_behavior": "Return updated value.", "risk": "low", "compatibility_assessment": "Signature unchanged.",
                "required_tests": ("Test compatibility.",), "uncertainty": "Runtime is not executed."},),
            evidence=(self.evidence,))
        tests = module("agents.07_test_engineer.schemas.output")
        tests_input = module("agents.07_test_engineer.schemas.input")
        test_id = "test_scenario_0123456789abcdef"
        test_output = tests.TestEngineerOutput(**identity("test_engineer"), status="proposed", llm_status="completed",
            upstream_statuses=tuple({"agent_id": name, "status": "completed"} for name in (
                "structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst",
                "migration_planner", "patch_author")), patch_status="awaiting_review",
            source_findings=(tests_input.TestFinding(agent_id="business_logic_analyst",
                finding_id=business_finding.finding_id, category="business_logic", title=business_finding.title,
                description=business_finding.description, severity="high", confidence=0.9, evidence=(self.evidence,)),),
            migration_tasks=({"task_id": task_id, "title": "Preserve API compatibility", "rationale": "Keep callers stable.",
                "evidence_ids": (self.evidence_id,), "priority": 1, "risk": "medium", "depends_on": (),
                "upstream_finding_ids": (), "expected_outcome": "Clients remain compatible."},),
            test_files=({"path": "tests/test_service.py", "language": "python", "frameworks": ("pytest",),
                "test_case_count": 1, "evidence_ids": ("evidence_test",), "analysis_status": "analyzed"},),
            patch_proposals=({"proposal_id": patch_id, "path": "app/service.py",
                "status": "awaiting_review", "validation_status": "passed", "unified_diff": diff,
                "original_content_hash": validated["original_content_hash"], "proposed_content_hash": validated["proposed_content_hash"],
                "related_finding_ids": (business_finding.finding_id,), "migration_task_ids": (task_id,),
                "evidence_ids": (self.evidence_id,), "rationale": "Test proposal association.",
                "expected_behavior": "Return updated value.", "risk": "low", "compatibility_assessment": "Signature stable.",
                "required_tests": ("Test compatibility.",), "uncertainty": "No execution."},),
            patch_analyses=({"proposal_id": patch_id, "analysis_status": "verified", "affected_path": "app/service.py",
                "affected_test_files": ("tests/test_service.py",), "evidence_ids": (self.evidence_id,),
                "assessment": "Diff applies in memory."},),
            scenarios=({"scenario_id": test_id, "scenario_type": "regression", "test_level": "unit",
                "title": "Preserve cancellation behavior", "priority": 1, "rationale": "Check the linked change.",
                "related_finding_ids": (business_finding.finding_id,), "migration_task_ids": (task_id,),
                "patch_proposal_ids": (patch_id,), "evidence_ids": (self.evidence_id, "evidence_test"),
                "preconditions": ("Service is initialized.",), "inputs": ("authorized request",),
                "steps": ("Call cancellation.",), "expected_results": ("Return updated value.",),
                "uncertainty": "Scenario not executed."},), evidence=(self.evidence, test_ev))
        request = WorkflowRequest(execution_id=self.ids["execution"], workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"], commit_sha=self.commit,
            manifest_digest=self.digest, correlation_id="binding", trace_id="binding")
        results = {"structure_analyzer": structure.model_dump(mode="json"),
            "business_logic_analyst": business_output.model_dump(mode="json"),
            "code_quality_analyst": quality_output.model_dump(mode="json"),
            "security_analyst": security_output.model_dump(mode="json"),
            "migration_planner": migration_output.model_dump(mode="json"),
            "patch_author": patch_output.model_dump(mode="json"),
            "test_engineer": test_output.model_dump(mode="json")}
        lifecycle = {agent: "completed" for agent in results}
        binder = module("agents.08_review_agent.services.input_binding").bind_review_input
        bound = binder(request, results, lifecycle)
        self.assertEqual({row.agent_id for row in bound.upstream_statuses if row.output_present}, set(results))
        self.assertEqual({row.agent_id for row in bound.findings}, {
            "business_logic_analyst", "code_quality_analyst", "security_analyst"})
        self.assertEqual(bound.repository.files_inspected, 2)
        self.assertIn("Cancellation", [fact.title for fact in bound.facts])
        self.assertIn("app/service.py", [fact.title for fact in bound.facts])
        self.assertEqual(bound.migration_tasks[0].task_id, task_id)
        self.assertEqual(bound.patch_proposals[0].source_finding_ids, (business_finding.finding_id,))
        self.assertEqual(bound.test_scenarios[0].execution_status, "not_executed")
        self.assertEqual(bound.test_scenarios[0].patch_proposal_ids, (patch_id,))
        execution_context = self.context.model_copy(update={"execution_id": bound.execution_id})
        model = FakeModel({"executive_summary": "The bound upstream context is ready for human review.",
            "summary_upstream_ids": ["business_logic_analyst"],
            "assessments": [], "conflicts": [], "uncertainties": []})
        _service.review_analysis(bound, execution_context,
            Reader({"app/service.py": self.raw, "tests/test_service.py": test_raw}), model)
        review_prompt = model.messages[1].content
        for upstream_content in ("Cancellation", "Complex service", "Authorization guard",
                                 "Preserve API compatibility", "Validated compatibility proposal",
                                 "Preserve cancellation behavior", '"files_inspected":2'):
            self.assertIn(upstream_content, review_prompt)
        stale = dict(results)
        stale_quality = dict(stale["code_quality_analyst"])
        stale_quality["snapshot_id"] = str(uuid.uuid4())
        stale["code_quality_analyst"] = stale_quality
        rejected = binder(request, stale, lifecycle)
        quality_status = next(row for row in rejected.upstream_statuses if row.agent_id == "code_quality_analyst")
        self.assertEqual(quality_status.status, "completed")
        self.assertFalse(quality_status.output_present)
        self.assertEqual(quality_status.validation_status, "rejected")
        self.assertNotIn("quality_finding_01", {row.finding_id for row in rejected.findings})
