import uuid
import threading
from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from llm_gateway.contracts import (GatewayRequest, LLMMessage, ModelRoute, ModelTier,
                                   ProviderResponse, Usage)
from llm_gateway.errors import (BudgetExceeded, InvalidProviderResponse, PolicyDenied,
                                ProviderFailure, ProviderUnavailable, RateLimited, UnknownModel,
                                ProviderCallError, ProviderFailureKind, PoolBudgetExceeded)
from llm_gateway.gateway import LLMGateway
from llm_gateway.policy import GatewayPolicy, RequestLimits
from llm_gateway.pool_contracts import (CredentialReference, PoolConfiguration, PoolConfigurations,
    PoolId, PoolLimits, PoolRetryPolicy, PoolRoute, QuotaIdentity, QuotaLimits)
from llm_gateway.credentials import EnvironmentCredentialResolver
from agent_foundation.permissions import Permission


class FakeProvider:
    def __init__(self, content=None, usage=None):
        self.content = content if content is not None else "ok"
        self.usage = usage or Usage(input_tokens=3, output_tokens=1, availability="available")
        self.calls = []
        self.requests = []

    def complete(self, route, request, *, timeout_seconds, credential=None):
        self.calls.append((route.model_id, timeout_seconds))
        self.requests.append(request)
        return ProviderResponse(content=self.content, usage=self.usage)


def make_request(**values):
    base = dict(agent_id="review_agent", execution_id=str(uuid.uuid4()),
        provider_id="fake", model_id="model-1", messages=(LLMMessage(role="user", content="inspect"),),
        correlation_id="corr", trace_id="trace")
    base.update(values)
    return GatewayRequest(**base)


def configured(provider, *, tier=ModelTier.FREE, limits=None, allow_paid=False,
               price_known=True, retry=None, circuit_breaker_failures=3):
    route = ModelRoute(provider_id="fake", model_id="model-1", tier=tier,
        supports_structured_output=True, cost_per_1k_input_tokens=0.01,
        cost_per_1k_output_tokens=0.02)
    if not price_known:
        route = route.model_copy(update={"cost_per_1k_input_tokens": None, "cost_per_1k_output_tokens": None})
    cred = CredentialReference(credential_id="test-credential", secret_ref="CODEWISE_LLM_CREDENTIAL_TEST",
        identity=QuotaIdentity(provider_id="fake", account_id="test-account", project_id="test-project",
                               quota_bucket_id="test-bucket"))
    request_limits = limits or RequestLimits()
    pool = PoolConfiguration(pool_id=PoolId.POOL_C, enabled=True, routes=(PoolRoute(route=route, credentials=(cred,)),),
        limits=PoolLimits(max_requests_per_minute=request_limits.max_requests_per_minute,
            max_requests_per_execution=request_limits.max_requests_per_execution,
            max_requests_per_agent_per_hour=request_limits.max_requests_per_agent,
            max_input_bytes_per_request=request_limits.max_input_bytes,
            max_output_tokens_per_request=max(32768, request_limits.max_output_tokens),
            max_input_tokens_per_hour=1_000_000, max_output_tokens_per_hour=1_000_000,
            max_concurrent_requests=request_limits.max_concurrent_requests,
            max_cost_per_request=request_limits.max_cost_per_request),
        quota_limits=QuotaLimits(max_requests_per_minute=1000, max_input_tokens_per_hour=1_000_000,
            max_output_tokens_per_hour=1_000_000, max_concurrent_requests=request_limits.max_concurrent_requests),
        retry=retry or PoolRetryPolicy(max_retries_per_credential=0,
            circuit_breaker_failures=circuit_breaker_failures), paid_authorized=(allow_paid or tier == ModelTier.PAID))
    policy = GatewayPolicy(enabled=True, allowed_agents={"review_agent"}, allowed_models={("fake", "model-1")},
        enabled_tiers={tier}, allow_paid=allow_paid, max_retries=1, circuit_breaker_failures=circuit_breaker_failures,
        limits=request_limits)
    configurations = PoolConfigurations.disabled().model_copy(update={"pools": {
        **PoolConfigurations.disabled().pools, PoolId.POOL_C: pool}})
    return LLMGateway(policy=policy, pool_configurations=configurations,
        credential_resolver=EnvironmentCredentialResolver({"CODEWISE_LLM_CREDENTIAL_TEST": "synthetic-test-secret"}),
        providers={"fake": provider})


class GatewayTests(SimpleTestCase):
    def test_default_is_closed_and_has_no_hidden_provider(self):
        gateway = LLMGateway()
        with self.assertRaises(PolicyDenied):
            gateway.invoke(make_request(), permissions={Permission.LLM_INVOKE})

    def test_permission_and_paid_model_policy_are_enforced(self):
        provider = FakeProvider()
        gateway = configured(provider, tier=ModelTier.PAID)
        with self.assertRaises(PolicyDenied):
            gateway.invoke(make_request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls, [])
        with self.assertRaises(Exception):
            gateway.invoke(make_request(), permissions=set())

    def test_route_task_capabilities_are_enforced(self):
        provider = FakeProvider()
        gateway = configured(provider)
        with self.assertRaises(PolicyDenied):
            gateway.invoke(make_request(task="migration"), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls, [])

    def test_known_route_response_and_unknown_usage_are_explicit(self):
        provider = FakeProvider(usage=Usage())
        response = configured(provider).invoke(make_request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(response.output, "ok")
        self.assertEqual(response.usage.availability, "unavailable")
        self.assertIsNone(response.usage.cost)
        self.assertEqual(response.provider_id, "fake")

    def test_structured_output_is_schema_validated(self):
        provider = FakeProvider(content={"decision": "ship"})
        request = make_request(output_schema={"type": "object", "required": ["decision"],
                                               "properties": {"decision": {"enum": ["ship", "hold"]}},
                                               "additionalProperties": False})
        response = configured(provider).invoke(request, permissions={Permission.LLM_INVOKE})
        self.assertEqual(response.output["decision"], "ship")
        bad = configured(FakeProvider(content={"decision": "maybe"}))
        with self.assertRaises(InvalidProviderResponse):
            bad.invoke(request, permissions={Permission.LLM_INVOKE})

    def test_cost_ceiling_fails_closed_when_price_or_usage_is_unavailable(self):
        request = make_request(max_cost=0.01)
        gateway = configured(FakeProvider(), price_known=False)
        with self.assertRaises(BudgetExceeded):
            gateway.invoke(request, permissions={Permission.LLM_INVOKE})

    def test_input_size_limit_is_enforced_before_provider_call(self):
        provider = FakeProvider()
        limits = RequestLimits(max_input_bytes=3)
        gateway = configured(provider, limits=limits)
        with self.assertRaises(BudgetExceeded):
            gateway.invoke(make_request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls, [])

    def test_deadline_is_passed_to_provider(self):
        provider = FakeProvider()
        gateway = configured(provider)
        request = make_request(deadline=datetime.now(timezone.utc) + timedelta(seconds=10))
        gateway.invoke(request, permissions={Permission.LLM_INVOKE})
        self.assertGreater(provider.calls[0][1], 0)

    def test_concurrency_limit_is_enforced(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingProvider(FakeProvider):
            def complete(self, route, request, *, timeout_seconds, credential=None):
                entered.set()
                release.wait(2)
                return super().complete(route, request, timeout_seconds=timeout_seconds, credential=credential)
        provider = BlockingProvider()
        gateway = configured(provider, limits=RequestLimits(max_concurrent_requests=1))
        first_errors = []
        first = threading.Thread(target=lambda: self._invoke_capturing(gateway, make_request(), first_errors))
        first.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(RateLimited):
            gateway.invoke(make_request(), permissions={Permission.LLM_INVOKE})
        release.set()
        first.join(2)
        self.assertFalse(first.is_alive())
        self.assertEqual(first_errors, [])

    def test_retry_requires_idempotence_and_explicit_retryable_error(self):
        class RetryProvider(FakeProvider):
            def __init__(self):
                super().__init__("ok")
                self.attempts = 0
            def complete(self, route, request, *, timeout_seconds, credential=None):
                self.attempts += 1
                if self.attempts == 1: raise ProviderCallError(ProviderFailureKind.NETWORK_RETRYABLE)
                return super().complete(route, request, timeout_seconds=timeout_seconds, credential=credential)
        provider = RetryProvider()
        gateway = configured(provider)
        gateway = configured(provider, retry=PoolRetryPolicy(max_retries_per_credential=1))
        with self.assertRaises(ProviderUnavailable):
            gateway.invoke(make_request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.attempts, 1)
        provider = RetryProvider()
        gateway = configured(provider, retry=PoolRetryPolicy(max_retries_per_credential=1))
        result = gateway.invoke(make_request(idempotent=True), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.attempts, 2)
        self.assertEqual(result.retries, 1)

    def test_provider_circuit_breaker_and_safe_metadata(self):
        class BrokenProvider(FakeProvider):
            def complete(self, route, request, *, timeout_seconds, credential=None): raise RuntimeError("secret provider exception")
        provider = BrokenProvider()
        gateway = configured(provider, circuit_breaker_failures=1)
        with self.assertRaises(ProviderFailure) as first:
            gateway.invoke(make_request(), permissions={Permission.LLM_INVOKE})
        self.assertNotIn("secret provider exception", str(first.exception))
        with self.assertRaises(ProviderUnavailable):
            gateway.invoke(make_request(), permissions={Permission.LLM_INVOKE})

        class SensitiveProvider(FakeProvider):
            def complete(self, route, request, *, timeout_seconds, credential=None):
                return ProviderResponse(content="ok", metadata={"api_key": "never-log"})
        with self.assertRaises(InvalidProviderResponse):
            configured(SensitiveProvider()).invoke(make_request(), permissions={Permission.LLM_INVOKE})

    def test_gateway_observer_receives_metadata_not_prompt(self):
        events = []
        gateway = configured(FakeProvider(),)
        gateway.observer = events.append
        gateway._pool_router.observer = events.append
        gateway.invoke(make_request(messages=(LLMMessage(role="user", content="private prompt"),)),
                       permissions={Permission.LLM_INVOKE})
        self.assertEqual(len(events), 1)
        self.assertNotIn("private prompt", str(events[0]))

    @staticmethod
    def _invoke_capturing(gateway, request, errors):
        try: gateway.invoke(request, permissions={Permission.LLM_INVOKE})
        except Exception as exc: errors.append(exc)
