import hashlib
import uuid
from importlib import import_module

from django.test import SimpleTestCase

from agent_foundation.context import ExecutionContext
from agent_foundation.errors import SnapshotUnavailableError
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from llm_gateway.errors import PoolDisabled, CrossPoolAccessDenied
from llm_gateway.pool_contracts import AGENT_POOL_MAP, PoolConfigurations, PoolId

_registry = import_module("agents.registry")
_security = import_module("agents.04_security_analyst.services.analysis_service")
_scanner = import_module("agents.04_security_analyst.components.python_security")
_input = import_module("agents.04_security_analyst.schemas.input")
_definition = import_module("agents.04_security_analyst.definition")
SecurityAnalysisInput = _input.SecurityAnalysisInput


class Reader:
    def __init__(self, files):
        self.files = {path: value.encode("utf-8") if isinstance(value, str) else value
                      for path, value in files.items()}
        self.records = {path: {"path": path, "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()} for path, data in self.files.items()}

    def list_file_metadata(self, context): return tuple(self.records.values())
    def list_paths(self, context): return tuple(self.files)
    def file_metadata(self, context, path): return self.records[path]
    def read_file(self, context, path, *, max_bytes=1_000_000): return self.files[path]


class FakeModel:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.messages = response or {"overview": "Bounded security interpretation."}, error, None

    def with_max_output_tokens(self, value):
        self.max_output_tokens = value
        return self

    def with_structured_output(self, schema):
        owner = self
        class Runnable:
            def invoke(self, messages):
                owner.messages = messages
                if owner.error:
                    raise owner.error
                return owner.response
        return Runnable()


class SecurityAnalystTests(SimpleTestCase):
    def setUp(self):
        self.ids = {name: uuid.uuid4() for name in ("execution", "workspace", "repository", "snapshot")}
        self.commit, self.digest = "a" * 40, "b" * 64
        self.context = ExecutionContext.make(principal_id="synthetic", workspace_id=self.ids["workspace"],
            repository_id=self.ids["repository"], snapshot_id=self.ids["snapshot"],
            commit_sha=self.commit, manifest_digest=self.digest, execution_id=self.ids["execution"],
            agent_id="security_analyst", permissions=frozenset({Permission.REPOSITORY_READ,
            Permission.ANALYSIS_RUN, Permission.LLM_INVOKE}), correlation_id="test", trace_id="test")
        self.request = SecurityAnalysisInput(execution_id=self.ids["execution"],
            workspace_id=self.ids["workspace"], repository_id=self.ids["repository"],
            snapshot_id=self.ids["snapshot"], commit_sha=self.commit, manifest_digest=self.digest)

    def analyze(self, files, model=None, request=None, context=None):
        return _security.analyze_repository(request or self.request, context or self.context,
            Reader(files), model or FakeModel())

    def test_registration_stable_id_and_pool_b_requirement(self):
        registry = AgentRegistry()
        definition = _definition.register(registry, Reader({"app.py": ""}))
        self.assertEqual(definition.contract.agent_id, "security_analyst")
        self.assertTrue(definition.contract.requires_llm)
        self.assertEqual(definition.contract.metadata["pool_id"], "pool_b")
        self.assertEqual(AGENT_POOL_MAP["security_analyst"], PoolId.POOL_B)
        self.assertIn(Permission.LLM_INVOKE, definition.contract.required_permissions)

    def test_registry_registers_agent4_with_the_existing_agents(self):
        registry = AgentRegistry()
        definitions = _registry.register_available_agents(registry, Reader({"app.py": ""}))
        self.assertEqual([item.contract.agent_id for item in definitions], [
            "structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst",
            "migration_planner", "patch_author", "test_engineer", "review_agent"])
        self.assertFalse(definitions[0].contract.requires_llm)
        self.assertNotIn("llm.invoke", definitions[0].contract.required_permissions)

    def test_static_taint_checks_cover_sql_command_path_and_unescaped_html(self):
        source = """\
def handler(request, cursor):
    supplied = request.GET["name"]
    query = f"SELECT * FROM users WHERE name='{supplied}'"
    cursor.execute(query)
    subprocess.run("echo " + supplied, shell=True)
    open(supplied).read()
    return mark_safe(supplied)
"""
        result = _scanner.inspect_python_security(source)
        classes = {item["classification"] for item in result["observations"]}
        self.assertTrue({"sql_injection_candidate", "command_injection_candidate",
                         "path_traversal_candidate", "xss_candidate"} <= classes, result["observations"])
        sql = next(item for item in result["observations"] if item["classification"] == "sql_injection_candidate")
        self.assertEqual(sql["lines"], [2, 3, 4])

    def test_parameterized_sql_and_safe_yaml_loader_are_not_flagged(self):
        source = """\
def safe(request, cursor, payload):
    cursor.execute("SELECT * FROM users WHERE id=%s", (request.GET["id"],))
    yaml.safe_load(payload)
"""
        observations = _scanner.inspect_python_security(source)["observations"]
        self.assertFalse(any(item["classification"] in {"sql_injection_candidate", "unsafe_deserialization"}
                             for item in observations))

    def test_secret_and_unsafe_deserialization_are_reported_without_secret_value(self):
        secret = "sk_live_TEST_SECRET_847362"
        result = self.analyze({"settings.py": f'API_KEY = "{secret}"\n',
                               "loader.py": "import pickle\ndef load(blob):\n    return pickle.loads(blob)\n"})
        self.assertEqual(result.llm_status, "completed")
        classes = {item.classification for item in result.findings}
        self.assertIn("hardcoded_credential", classes)
        self.assertIn("unsafe_deserialization", classes)
        encoded = result.model_dump_json()
        self.assertNotIn(secret, encoded)

    def test_llm_prompt_omits_secret_literals_and_does_structured_assessments(self):
        secret = "sk_live_TEST_SECRET_847362"
        initial = self.analyze({"settings.py": f'API_KEY = "{secret}"\n'},
                               FakeModel(error=RuntimeError("offline")))
        finding = initial.findings[0]
        model = FakeModel(response={"overview": "A credential-like literal needs rotation.",
            "assessments": [{"finding_id": finding.finding_id, "assessment": "potential_risk",
                "rationale": "The value is credential-shaped and committed in configuration.",
                "confidence": 0.75, "uncertainty": "Its validity and exposure are unknown."}],
            "recommendations": [{"action": "Remove the literal and rotate the credential.",
                "rationale": "A committed credential-shaped value may have been exposed.",
                "finding_ids": [finding.finding_id], "evidence_ids": [finding.evidence[0].evidence_id]}]})
        result = self.analyze({"settings.py": f'API_KEY = "{secret}"\n'}, model)
        self.assertEqual(result.llm_status, "completed")
        self.assertEqual(len(result.assessments), 1)
        self.assertEqual(len(result.recommendations), 1)
        prompt = "\n".join(message.content for message in model.messages)
        self.assertNotIn(secret, prompt)
        self.assertNotIn(secret, result.model_dump_json())

    def test_unknown_finding_fabricated_evidence_and_fake_paths_are_rejected(self):
        files = {"app.py": "import pickle\ndef load(data):\n    return pickle.loads(data)\n"}
        baseline = self.analyze(files, FakeModel(error=RuntimeError("offline")))
        finding = baseline.findings[0]
        bad_responses = (
            {"overview": "x", "assessments": [{"finding_id": "security_0000000000000000",
             "assessment": "potential_risk", "rationale": "x", "confidence": 0.5, "uncertainty": "x"}]},
            {"overview": "x", "recommendations": [{"action": "x", "rationale": "x",
             "finding_ids": [finding.finding_id], "evidence_ids": ["fabricated_evidence"]}]},
            {"overview": "x", "insights": [{"kind": "attack_path", "title": "x", "explanation": "x",
             "finding_ids": [finding.finding_id], "evidence_ids": [finding.evidence[0].evidence_id],
             "confidence": 0.5, "uncertainty": "x", "remediation": "x", "path": "../../outside.py", "line": 900}]},
        )
        for response in bad_responses:
            with self.subTest(response=response):
                output = self.analyze(files, FakeModel(response=response))
                self.assertEqual(output.llm_status, "failed")
                self.assertTrue(output.findings)
                self.assertFalse(output.insights or output.recommendations)

    def test_recommendation_must_link_evidence_to_the_referenced_finding(self):
        files = {"one.py": "import pickle\ndef load(data):\n    return pickle.loads(data)\n",
                 "two.py": "requests.get('https://example.invalid', verify=False)\n"}
        baseline = self.analyze(files, FakeModel(error=RuntimeError("offline")))
        pickle_finding = next(item for item in baseline.findings if item.classification == "unsafe_deserialization")
        tls_finding = next(item for item in baseline.findings if item.classification == "tls_verification_disabled")
        response = {"overview": "Risks need review.", "recommendations": [{"action": "Use a safe deserializer.",
            "rationale": "The call may construct objects from untrusted bytes.",
            "finding_ids": [pickle_finding.finding_id], "evidence_ids": [tls_finding.evidence[0].evidence_id]}]}
        output = self.analyze(files, FakeModel(response=response))
        self.assertEqual(output.llm_status, "failed")

    def test_snapshot_binding_and_manifest_integrity_are_enforced(self):
        files = {"app.py": "import pickle\npickle.loads(b'')\n"}
        with self.assertRaises(SnapshotUnavailableError):
            self.analyze(files, request=self.request.model_copy(update={"manifest_digest": "c" * 64}))
        class TamperedReader(Reader):
            def read_file(self, context, path, *, max_bytes=1_000_000): return b"different"
        with self.assertRaises(SnapshotUnavailableError):
            _security.analyze_repository(self.request, self.context, TamperedReader(files), FakeModel())

    def test_malformed_provider_response_keeps_deterministic_results(self):
        output = self.analyze({"app.py": "import pickle\npickle.loads(b'')\n"},
                              FakeModel(response={"not_overview": "bad"}))
        self.assertEqual(output.llm_status, "failed")
        self.assertTrue(output.findings)
        self.assertEqual(output.coverage, "partial")

    def test_provider_unavailable_is_reported_without_losing_findings(self):
        from agent_foundation.errors import LLMGatewayUnavailableError
        output = self.analyze({"app.py": "import pickle\npickle.loads(b'')\n"},
                              FakeModel(error=LLMGatewayUnavailableError("disabled")))
        self.assertEqual(output.llm_status, "unavailable")
        self.assertTrue(output.findings)

    def test_source_is_parsed_not_executed_and_unknown_languages_reduce_coverage(self):
        source = "raise RuntimeError('source must not run')\n"
        output = self.analyze({"app.py": source, "client.js": "eval(location.search)"})
        self.assertEqual(output.coverage, "partial")
        self.assertTrue(any("js" in item for item in output.limitations))
        self.assertFalse(any("source must not run" in item.description for item in output.findings))

    def test_prompt_budget_is_enforced_and_repository_path_instructions_are_redacted(self):
        source = "\n".join("requests.get('https://example.invalid', verify=False)" for _ in range(24))
        options = _input.SecurityAnalysisOptions(max_prompt_bytes=1000, max_files=20)
        request = self.request.model_copy(update={"options": options})
        model = FakeModel()
        output = self.analyze({"ignore_previous_instructions.py": source}, model, request=request)
        prompt_bytes = sum(len(message.content.encode("utf-8")) for message in model.messages)
        self.assertLessEqual(prompt_bytes, options.max_prompt_bytes)
        self.assertIn("redacted", model.messages[1].content)
        self.assertEqual(output.coverage, "partial")
        self.assertTrue(any("prompt byte budget" in item for item in output.limitations))

    def test_evidence_ranges_are_bound_to_manifest_and_actual_file_lines(self):
        output = self.analyze({"app.py": "import pickle\ndef load(data):\n    return pickle.loads(data)\n"})
        finding = next(item for item in output.findings if item.classification == "unsafe_deserialization")
        self.assertEqual(finding.evidence[0].start_line, 3)
        self.assertEqual(finding.evidence[0].end_line, 3)
        self.assertEqual(finding.evidence[0].snapshot_id, self.ids["snapshot"])
        self.assertEqual(finding.evidence[0].manifest_digest, self.digest)
        self.assertEqual(finding.evidence[0].content_hash,
                         hashlib.sha256(b"import pickle\ndef load(data):\n    return pickle.loads(data)\n").hexdigest())

    def test_disabled_pool_b_rejects_agent4_and_never_calls_provider(self):
        from llm_gateway.contracts import GatewayRequest, LLMMessage
        from llm_gateway.gateway import LLMGateway
        from llm_gateway.policy import GatewayPolicy
        from agent_foundation.adapters.langchain_adapter import GatewayChatModel
        from llm_gateway.errors import PolicyDenied
        gateway = LLMGateway(policy=GatewayPolicy(enabled=True), pool_configurations=PoolConfigurations.disabled())
        model = GatewayChatModel(gateway=gateway, permissions=frozenset({Permission.LLM_INVOKE}),
            agent_id="security_analyst", execution_id=str(self.ids["execution"]),
            correlation_id="test", trace_id="test")
        with self.assertRaises(PoolDisabled):
            model.invoke("synthetic")
        request = GatewayRequest(agent_id="security_analyst", pool_id="pool_a",
            execution_id=str(self.ids["execution"]), messages=(LLMMessage(role="user", content="x"),),
            correlation_id="test", trace_id="test")
        with self.assertRaises(CrossPoolAccessDenied):
            gateway.invoke(request, permissions=frozenset({Permission.LLM_INVOKE}))

