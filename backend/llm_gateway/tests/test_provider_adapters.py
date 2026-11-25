"""Offline contract tests for concrete provider adapters and gateway registration."""

import json
import sys
import types as python_types
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic import SecretStr

from agent_foundation.permissions import Permission
from llm_gateway.contracts import GatewayRequest, LLMMessage, ModelRoute, ModelTier
from llm_gateway.credentials import ResolvedCredential
from llm_gateway.errors import (PoolConfigurationError, ProviderCallError,
                                ProviderFailureKind, ProviderRejected)
from llm_gateway.gateway import LLMGateway
from llm_gateway.policy import GatewayPolicy, RequestLimits
from llm_gateway.pool_contracts import (CredentialReference, PoolConfiguration,
    PoolConfigurations, PoolId, PoolLimits, PoolRoute, QuotaIdentity, QuotaLimits)
from llm_gateway.provider_adapters import GeminiProviderAdapter, GroqProviderAdapter
from llm_gateway.provider_registry import ProviderAdapterRegistry


SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}
CAPABILITIES = frozenset({"structured_output", "system_messages", "max_output_tokens", "temperature", "top_p", "seed",
                          "stop_sequences"})


def make_request(*, agent="code_quality_analyst", pool_id="pool_a", output_schema=SCHEMA, **kwargs):
    values = dict(agent_id=agent, pool_id=pool_id, execution_id=str(uuid.uuid4()),
        messages=(LLMMessage(role="system", content="system context"),
                  LLMMessage(role="user", content="analyze"),
                  LLMMessage(role="assistant", content="prior response")),
        output_schema=output_schema, correlation_id="corr-test", trace_id="trace-test",
        max_output_tokens=64, temperature=0.2, top_p=0.9, seed=7, stop=("END",),
        idempotent=True)
    values.update(kwargs)
    return GatewayRequest(**values)


def make_route(provider="groq", model="model-under-test"):
    return ModelRoute(provider_id=provider, model_id=model, tier=ModelTier.FREE,
        supported_tasks={"analysis"}, capabilities=CAPABILITIES - {"structured_output"},
        supports_structured_output=True)


class FakeGroqCompletions:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeGroqClient:
    def __init__(self, response=None, error=None):
        self.chat = SimpleNamespace(completions=FakeGroqCompletions(response, error))
        self.closed = False

    def close(self):
        self.closed = True


class FakeGeminiModels:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeGeminiClient:
    def __init__(self, response=None, error=None):
        self.models = FakeGeminiModels(response, error)
        self.closed = False

    def close(self):
        self.closed = True


class FakeResolver:
    def resolve(self, reference):
        return ResolvedCredential(reference.credential_id, SecretStr("synthetic-offline-secret"))


def fake_groq_factory(client):
    calls = []
    def create_client(*, api_key, timeout_seconds):
        calls.append((api_key, timeout_seconds))
        return client
    return create_client, calls


def fake_gemini_factory(client):
    calls = []
    def create_client(*, api_key, timeout_seconds):
        calls.append((api_key, timeout_seconds))
        return client
    return create_client, calls


def status_error(name, status, *, headers=None, code=None):
    response = SimpleNamespace(headers=headers or {})
    cls = type(name, (Exception,), {})
    error = cls("synthetic exception text must not escape")
    error.status_code = status
    error.code = code if code is not None else status
    error.response = response
    return error


class ProviderAdapterTests(SimpleTestCase):
    def setUp(self):
        self.credential = ResolvedCredential("offline-test", SecretStr("synthetic-offline-secret"))

    def test_gemini_default_client_forwards_api_version_timeout_and_single_attempt(self):
        captured = {}
        google_module = python_types.ModuleType("google")
        genai_module = python_types.ModuleType("google.genai")
        sdk_types = python_types.ModuleType("google.genai.types")
        def http_options(**kwargs):
            captured["http_options"] = kwargs
            return kwargs
        def retry_options(**kwargs):
            captured["retry_options"] = kwargs
            return kwargs
        def client(**kwargs):
            captured["client"] = kwargs
            return object()
        sdk_types.HttpOptions = http_options
        sdk_types.HttpRetryOptions = retry_options
        genai_module.Client = client
        google_module.genai = genai_module
        with patch.dict(sys.modules, {"google": google_module, "google.genai": genai_module,
                                      "google.genai.types": sdk_types}):
            GeminiProviderAdapter._default_client(api_key="synthetic-offline-secret",
                timeout_seconds=4.25, api_version="v1beta")
        self.assertEqual(captured["http_options"]["api_version"], "v1beta")
        self.assertEqual(captured["http_options"]["timeout"], 4250)
        self.assertEqual(captured["retry_options"], {"attempts": 1})
        self.assertEqual(captured["client"]["api_key"], "synthetic-offline-secret")

    def test_groq_structured_call_maps_messages_options_usage_and_secret_injection(self):
        client = FakeGroqClient(response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}', refusal=None),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5), id="req-groq"))
        factory, created = fake_groq_factory(client)
        adapter = GroqProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES},
                                      client_factory=factory)
        response = adapter.complete(make_route(), make_request(), timeout_seconds=4.5,
                                    credential=self.credential)
        call = client.chat.completions.calls[0]
        self.assertEqual(call["model"], "model-under-test")
        self.assertEqual(call["max_completion_tokens"], 64)
        self.assertEqual(call["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertFalse(call["response_format"]["json_schema"]["strict"])
        self.assertEqual(call["messages"][0]["role"], "system")
        self.assertEqual(call["seed"], 7)
        self.assertEqual(response.content, {"answer": "ok"})
        self.assertEqual((response.usage.input_tokens, response.usage.output_tokens), (11, 5))
        self.assertEqual(created, [("synthetic-offline-secret", 4.5)])
        self.assertTrue(client.closed)

    def test_groq_normalizes_rate_limit_retry_after_and_hides_sdk_exception_text(self):
        client = FakeGroqClient(error=status_error("RateLimitError", 429, headers={"retry-after": "2.5"}))
        factory, _ = fake_groq_factory(client)
        adapter = GroqProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES}, client_factory=factory)
        with self.assertRaises(ProviderCallError) as caught:
            adapter.complete(make_route(), make_request(), timeout_seconds=5, credential=self.credential)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(caught.exception.retry_after_seconds, 2.5)
        self.assertNotIn("synthetic exception text", str(caught.exception))

    def test_groq_normalizes_auth_model_server_and_structured_capability_errors(self):
        expectations = [(401, ProviderFailureKind.AUTHENTICATION, False),
                        (404, ProviderFailureKind.UNSUPPORTED_MODEL, False),
                        (503, ProviderFailureKind.SERVER_TRANSIENT, False),
                        (400, ProviderFailureKind.UNSUPPORTED_CAPABILITY, True)]
        for status, expected, structured in expectations:
            with self.subTest(status=status):
                client = FakeGroqClient(error=status_error("APIStatusError", status))
                factory, _ = fake_groq_factory(client)
                adapter = GroqProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES}, client_factory=factory)
                with self.assertRaises(ProviderCallError) as caught:
                    adapter.complete(make_route(), make_request(output_schema=SCHEMA if structured else None),
                                     timeout_seconds=5, credential=self.credential)
                self.assertEqual(caught.exception.kind, expected)

    def test_groq_fails_before_transport_for_unverified_or_unsupported_model_features(self):
        client = FakeGroqClient(response=SimpleNamespace(choices=[]))
        factory, _ = fake_groq_factory(client)
        adapter = GroqProviderAdapter(model_capabilities={"model-under-test": {"temperature"}}, client_factory=factory)
        with self.assertRaises(ValueError):
            adapter.validate_route(make_route())
        with self.assertRaises(ProviderCallError) as caught:
            adapter.complete(make_route(), make_request(), timeout_seconds=5, credential=self.credential)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.UNSUPPORTED_CAPABILITY)
        self.assertEqual(client.chat.completions.calls, [])

    def test_sdk_client_construction_errors_are_normalized_without_leaking_text(self):
        def broken_factory(**kwargs):
            raise RuntimeError("synthetic-offline-secret must not escape")
        adapter = GroqProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES},
                                      client_factory=broken_factory)
        with self.assertRaises(ProviderCallError) as caught:
            adapter.complete(make_route(), make_request(), timeout_seconds=5,
                             credential=self.credential)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.PERMANENT)
        self.assertNotIn("synthetic-offline-secret", str(caught.exception))

    def test_gemini_rejects_system_only_request_without_transport(self):
        client = FakeGeminiClient(response=SimpleNamespace())
        factory, _ = fake_gemini_factory(client)
        adapter = GeminiProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES},
                                        client_factory=factory)
        request = make_request(messages=(LLMMessage(role="system", content="only system"),))
        with self.assertRaises(ProviderCallError) as caught:
            adapter.complete(make_route("gemini"), request, timeout_seconds=5,
                             credential=self.credential)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.INVALID_REQUEST)
        self.assertEqual(client.models.calls, [])

    def test_gemini_structured_call_maps_content_options_usage_and_secret_injection(self):
        client = FakeGeminiClient(response=SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason="STOP")],
            prompt_feedback=None, parsed={"answer": "ok"}, text='{"answer":"ok"}',
            usage_metadata=SimpleNamespace(prompt_token_count=13, candidates_token_count=6),
            response_id="req-gemini", model_version="reported-version"))
        factory, created = fake_gemini_factory(client)
        adapter = GeminiProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES},
                                        client_factory=factory)
        response = adapter.complete(make_route("gemini"), make_request(), timeout_seconds=6,
                                    credential=self.credential)
        call = client.models.calls[0]
        self.assertEqual(call["model"], "model-under-test")
        self.assertEqual(call["config"]["response_mime_type"], "application/json")
        self.assertEqual(call["config"]["response_json_schema"], SCHEMA)
        self.assertEqual(call["config"]["system_instruction"], "system context")
        self.assertEqual(call["contents"][0]["role"], "user")
        self.assertEqual(call["contents"][1]["role"], "model")
        self.assertEqual((response.usage.input_tokens, response.usage.output_tokens), (13, 6))
        self.assertEqual(response.metadata["model_version"], "reported-version")
        self.assertEqual(created, [("synthetic-offline-secret", 6)])
        self.assertTrue(client.closed)

    def test_gemini_normalizes_quota_timeout_safety_and_invalid_json(self):
        cases = [
            (status_error("ClientError", 429, code=429), ProviderFailureKind.RATE_LIMIT, "error"),
            (type("APITimeoutError", (Exception,), {})("timeout"), ProviderFailureKind.NETWORK_RETRYABLE, "error"),
        ]
        for error, expected, _ in cases:
            client = FakeGeminiClient(error=error)
            factory, _ = fake_gemini_factory(client)
            adapter = GeminiProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES}, client_factory=factory)
            with self.assertRaises(ProviderCallError) as caught:
                adapter.complete(make_route("gemini"), make_request(), timeout_seconds=5, credential=self.credential)
            self.assertEqual(caught.exception.kind, expected)

        blocked = FakeGeminiClient(response=SimpleNamespace(
            prompt_feedback=SimpleNamespace(block_reason="SAFETY"), candidates=[], text=None,
            usage_metadata=None))
        factory, _ = fake_gemini_factory(blocked)
        adapter = GeminiProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES}, client_factory=factory)
        with self.assertRaises(ProviderCallError) as caught:
            adapter.complete(make_route("gemini"), make_request(), timeout_seconds=5, credential=self.credential)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.SAFETY_REFUSAL)

        malformed = FakeGeminiClient(response=SimpleNamespace(
            prompt_feedback=None, candidates=[SimpleNamespace(finish_reason="STOP")],
            parsed=None, text="not json", usage_metadata=None))
        factory, _ = fake_gemini_factory(malformed)
        adapter = GeminiProviderAdapter(model_capabilities={"model-under-test": CAPABILITIES}, client_factory=factory)
        with self.assertRaises(ProviderCallError) as caught:
            adapter.complete(make_route("gemini"), make_request(), timeout_seconds=5, credential=self.credential)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.STRUCTURED_OUTPUT_INVALID)

    def test_gemini_unverified_capability_is_rejected_before_provider_call(self):
        client = FakeGeminiClient(response=SimpleNamespace())
        factory, _ = fake_gemini_factory(client)
        adapter = GeminiProviderAdapter(model_capabilities={"model-under-test": {"temperature"}}, client_factory=factory)
        with self.assertRaises(ValueError):
            adapter.validate_route(make_route("gemini"))
        self.assertEqual(client.models.calls, [])


class ProviderRegistryIntegrationTests(SimpleTestCase):
    def test_registry_adapters_are_selected_by_existing_pool_and_gateway_contract(self):
        groq_client = FakeGroqClient(response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"groq"}', refusal=None),
                                     finish_reason="stop")], usage=None))
        gemini_client = FakeGeminiClient(response=SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason="STOP")], prompt_feedback=None,
            parsed={"answer": "gemini"}, text='{"answer":"gemini"}', usage_metadata=None))
        groq_factory, _ = fake_groq_factory(groq_client)
        gemini_factory, _ = fake_gemini_factory(gemini_client)
        registry = ProviderAdapterRegistry.groq_and_gemini(
            model_capabilities={"groq": {"groq-test-model": CAPABILITIES},
                                "gemini": {"gemini-test-model": CAPABILITIES}},
            groq_client_factory=groq_factory, gemini_client_factory=gemini_factory)

        def active_pool(pool_id, provider, model, agent_key):
            credential = CredentialReference(credential_id=f"{pool_id.value}-{agent_key}",
                secret_ref=f"CODEWISE_LLM_CREDENTIAL_{pool_id.value.upper()}_{agent_key.upper()}",
                identity=QuotaIdentity(provider_id=provider, account_id=f"account-{pool_id.value}",
                    project_id=f"project-{pool_id.value}", quota_bucket_id=f"bucket-{pool_id.value}"))
            return PoolConfiguration(pool_id=pool_id, enabled=True, routes=(PoolRoute(
                route=make_route(provider, model), credentials=(credential,)),),
                limits=PoolLimits(max_requests_per_minute=10, max_requests_per_execution=5,
                    max_requests_per_agent_per_hour=20, max_input_bytes_per_request=1000,
                    max_output_tokens_per_request=100, max_input_tokens_per_hour=10000,
                    max_output_tokens_per_hour=10000, max_concurrent_requests=2),
                quota_limits=QuotaLimits(max_requests_per_minute=10, max_input_tokens_per_hour=10000,
                    max_output_tokens_per_hour=10000, max_concurrent_requests=2))

        pools = PoolConfigurations(pools={
            PoolId.POOL_A: active_pool(PoolId.POOL_A, "groq", "groq-test-model", "primary"),
            PoolId.POOL_B: active_pool(PoolId.POOL_B, "gemini", "gemini-test-model", "primary"),
            PoolId.POOL_C: PoolConfiguration(pool_id=PoolId.POOL_C),
        })
        policy = GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE},
                               limits=RequestLimits(max_output_tokens=100))
        gateway = LLMGateway(policy=policy, pool_configurations=pools,
                             credential_resolver=FakeResolver(), providers=registry)
        a = gateway.invoke(make_request(agent="code_quality_analyst", pool_id="pool_a"),
                           permissions={Permission.LLM_INVOKE})
        b = gateway.invoke(make_request(agent="security_analyst", pool_id="pool_b"),
                           permissions={Permission.LLM_INVOKE})
        self.assertEqual((a.provider_id, a.pool_id, a.output["answer"]), ("groq", "pool_a", "groq"))
        self.assertEqual((b.provider_id, b.pool_id, b.output["answer"]), ("gemini", "pool_b", "gemini"))

    def test_enabled_routes_fail_closed_before_calls_when_model_capability_is_unverified(self):
        registry = ProviderAdapterRegistry.groq_and_gemini(model_capabilities={"groq": {}, "gemini": {}})
        credential = CredentialReference(credential_id="unknown-model", secret_ref="CODEWISE_LLM_CREDENTIAL_UNKNOWN",
            identity=QuotaIdentity(provider_id="groq", account_id="a", project_id="p", quota_bucket_id="q"))
        pool = PoolConfiguration(pool_id=PoolId.POOL_A, enabled=True,
            routes=(PoolRoute(route=make_route("groq", "unknown-model"), credentials=(credential,)),),
            limits=PoolLimits(max_requests_per_minute=10, max_requests_per_execution=5,
                max_requests_per_agent_per_hour=20, max_input_bytes_per_request=1000,
                max_output_tokens_per_request=100, max_input_tokens_per_hour=10000,
                max_output_tokens_per_hour=10000, max_concurrent_requests=2),
            quota_limits=QuotaLimits(max_requests_per_minute=10, max_input_tokens_per_hour=10000,
                max_output_tokens_per_hour=10000, max_concurrent_requests=2))
        with self.assertRaises(PoolConfigurationError):
            LLMGateway(policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE}),
                pool_configurations=PoolConfigurations(pools={PoolId.POOL_A: pool,
                    PoolId.POOL_B: PoolConfiguration(pool_id=PoolId.POOL_B),
                    PoolId.POOL_C: PoolConfiguration(pool_id=PoolId.POOL_C)}),
                credential_resolver=FakeResolver(), providers=registry)

