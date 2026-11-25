import uuid
import ast
from pathlib import Path

from django.test import SimpleTestCase
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from agent_foundation.adapters.langchain_adapter import GatewayChatModel
from agent_foundation.permissions import Permission
from agent_foundation.context import ExecutionContext
from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.contracts.execution import ExecutionRequest, ExecutionStatus
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.registry import AgentDefinition, AgentRegistry
from llm_gateway.contracts import ModelRoute, ModelTier, ProviderResponse, Usage
from llm_gateway.gateway import LLMGateway
from llm_gateway.policy import GatewayPolicy
from llm_gateway.pool_contracts import (CredentialReference, PoolConfiguration, PoolConfigurations,
    PoolId, PoolLimits, PoolRoute, QuotaIdentity, QuotaLimits)
from llm_gateway.credentials import EnvironmentCredentialResolver
from llm_gateway.policy import RequestLimits


class FakeProvider:
    def __init__(self, content):
        self.content = content
        self.requests = []
    def complete(self, route, request, *, timeout_seconds, credential=None):
        self.requests.append(request)
        return ProviderResponse(content=self.content, usage=Usage(input_tokens=4, output_tokens=2))


class LangChainGatewayTests(SimpleTestCase):
    def gateway(self, provider):
        route = ModelRoute(provider_id="fake", model_id="test", tier=ModelTier.FREE, supports_structured_output=True)
        credential = CredentialReference(credential_id="langchain-test", secret_ref="CODEWISE_LLM_CREDENTIAL_TEST",
            identity=QuotaIdentity(provider_id="fake", account_id="test-account", project_id="test-project",
                                   quota_bucket_id="test-bucket"))
        limits = PoolLimits(max_requests_per_minute=100, max_requests_per_execution=20,
            max_requests_per_agent_per_hour=100, max_input_bytes_per_request=100000,
            max_output_tokens_per_request=32768, max_input_tokens_per_hour=100000,
            max_output_tokens_per_hour=100000, max_concurrent_requests=10)
        pool = PoolConfiguration(pool_id=PoolId.POOL_C, enabled=True,
            routes=(PoolRoute(route=route, credentials=(credential,)),), limits=limits,
            quota_limits=QuotaLimits(max_requests_per_minute=100, max_input_tokens_per_hour=100000,
                max_output_tokens_per_hour=100000, max_concurrent_requests=10))
        pools = PoolConfigurations.disabled().model_copy(update={"pools": {
            **PoolConfigurations.disabled().pools, PoolId.POOL_C: pool}})
        policy = GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE})
        return LLMGateway(policy=policy, pool_configurations=pools,
            credential_resolver=EnvironmentCredentialResolver({"CODEWISE_LLM_CREDENTIAL_TEST": "synthetic-test-secret"}),
            providers={"fake": provider})

    def model(self, content):
        gateway = self.gateway(FakeProvider(content))
        return GatewayChatModel(gateway=gateway, permissions=frozenset({Permission.LLM_INVOKE}),
            agent_id="review_agent", execution_id=str(uuid.uuid4()), correlation_id="c", trace_id="t",
            provider_id="fake", model_id="test")

    def test_chat_prompt_runnable_calls_only_gateway(self):
        model = self.model("looks safe")
        chain = ChatPromptTemplate.from_template("Review {change}") | model.bind(temperature=0.2, stop=["END"])
        response = chain.invoke({"change": "small refactor"})
        self.assertEqual(response.content, "looks safe")
        self.assertEqual(response.response_metadata["provider_id"], "fake")
        provider = model._gateway.providers["fake"]
        self.assertEqual(provider.requests[0].temperature, 0.2)
        self.assertEqual(provider.requests[0].stop, ("END",))

    def test_structured_output_is_a_runnable_and_gateway_validates_it(self):
        model = self.model({"approved": True})
        output = model.with_structured_output({"type": "object", "required": ["approved"],
                                               "properties": {"approved": {"type": "boolean"}}}).invoke("review")
        self.assertEqual(output, {"approved": True})

    def test_missing_permission_is_denied(self):
        model = self.model("should not be called").model_copy(update={"permissions": frozenset()})
        with self.assertRaises(Exception): model.invoke("prompt")

    def test_llm_agent_lifecycle_receives_gateway_model_and_reports_usage(self):
        class Input(BaseModel): text: str
        class Output(BaseModel): summary: str
        registry = AgentRegistry()
        definition = AgentDefinition.create(agent_id="review_agent", display_name="Review", description="review test",
            version="1", input_model=Input, output_model=Output,
            runner=lambda value, context, model: {"summary": model.invoke(value.text).content},
            capabilities={AgentCapability.LLM_REASONING}, required_permissions={Permission.LLM_INVOKE}, requires_llm=True)
        registry.register(definition)
        request = ExecutionRequest(execution_id=uuid.uuid4(), agent_id="review_agent", agent_version="1",
            input={"text": "review"}, workspace_id=uuid.uuid4(), repository_id=uuid.uuid4(), snapshot_id=uuid.uuid4(),
            commit_sha="a" * 40, manifest_digest="b" * 64, correlation_id="c", trace_id="t")
        context = ExecutionContext.make(principal_id="test", workspace_id=request.workspace_id,
            repository_id=request.repository_id, snapshot_id=request.snapshot_id, commit_sha=request.commit_sha,
            manifest_digest=request.manifest_digest, execution_id=request.execution_id, agent_id="review_agent",
            permissions=frozenset({Permission.LLM_INVOKE}), correlation_id="c", trace_id="t")
        gateway = self.gateway(FakeProvider("reviewed"))
        lifecycle = ExecutionLifecycle(registry, chat_model_factory=lambda definition, req, ctx:
            GatewayChatModel(gateway=gateway, permissions=ctx.permissions, agent_id=ctx.agent_id,
                execution_id=ctx.execution_id, correlation_id=ctx.correlation_id, trace_id=ctx.trace_id,
                provider_id="fake", model_id="test"))
        result = lifecycle.execute(request, context)
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(result.output.summary, "reviewed")
        self.assertEqual(result.model_usage.provider, "fake")
        self.assertEqual(result.model_usage.input_tokens, 4)

    def test_provider_sdks_are_not_imported_outside_gateway(self):
        backend = Path(__file__).resolve().parents[2]
        banned = {"openai", "anthropic", "groq", "google.generativeai", "cohere", "boto3"}
        for path in backend.rglob("*.py"):
            if "llm_gateway" in path.parts or "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(any(name == prefix or name.startswith(prefix + ".")
                                     for name in names for prefix in banned), str(path))
