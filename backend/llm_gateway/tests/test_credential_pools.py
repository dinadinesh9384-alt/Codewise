import json
import threading
import uuid
from pydantic import BaseModel, SecretStr

from django.test import SimpleTestCase

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition
from llm_gateway.contracts import GatewayRequest, LLMMessage, ModelRoute, ModelTier, ProviderResponse, Usage
from llm_gateway.credentials import (CredentialResolutionError, EnvironmentCredentialResolver,
                                     ResolvedCredential)
from llm_gateway.errors import (CrossPoolAccessDenied, PoolBudgetExceeded, PoolDisabled, PoolExhausted,
                                PolicyDenied, ProviderCallError, ProviderFailureKind, ProviderRejected,
                                UnknownAgentPool, RateLimited)
from llm_gateway.gateway import LLMGateway
from llm_gateway.pool_contracts import (AGENT_POOL_MAP, CredentialReference, PoolConfiguration,
    PoolConfigurations, PoolId, PoolLimits, PoolRetryPolicy, PoolRoute, QuotaIdentity, QuotaLimits)
from llm_gateway.policy import GatewayPolicy, RequestLimits


class FakeResolver:
    def __init__(self, secrets): self.secrets = dict(secrets)
    def resolve(self, reference):
        value = self.secrets.get(reference.secret_ref)
        if not value: raise CredentialResolutionError("missing")
        return ResolvedCredential(reference.credential_id, SecretStr(value))


class FakePoolProvider:
    def __init__(self, behavior=None):
        self.behavior = behavior or {}
        self.calls = []
        self.entered = threading.Event()
        self.release = None

    def complete(self, route, request, *, timeout_seconds, credential):
        self.calls.append((route.provider_id, route.model_id, credential.credential_id))
        if self.release is not None:
            self.entered.set()
            self.release.wait(2)
        result = self.behavior.get(credential.credential_id)
        if isinstance(result, list) and result:
            result = result.pop(0)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(route, request, credential)
        return ProviderResponse(content={"ok": True}, usage=Usage(input_tokens=3, output_tokens=2))


def reference(credential_id, slot, *, account="account-1", project="project-1", quota="bucket-1", provider="synthetic"):
    return CredentialReference(credential_id=credential_id, secret_ref=f"CODEWISE_LLM_CREDENTIAL_{slot}",
        identity=QuotaIdentity(provider_id=provider, account_id=account, project_id=project,
                               quota_bucket_id=quota))


def pool_limits(**overrides):
    values = dict(max_requests_per_minute=50, max_requests_per_execution=8,
        max_requests_per_agent_per_hour=80, max_input_bytes_per_request=1000,
        max_output_tokens_per_request=16, max_input_tokens_per_hour=10_000,
        max_output_tokens_per_hour=10_000, max_concurrent_requests=4)
    values.update(overrides)
    return PoolLimits(**values)


def quota_limits(**overrides):
    values = dict(max_requests_per_minute=50, max_input_tokens_per_hour=10_000,
        max_output_tokens_per_hour=10_000, max_concurrent_requests=4)
    values.update(overrides)
    return QuotaLimits(**values)


def configured_pool(pool_id=PoolId.POOL_A, *, credentials=None, provider="synthetic", model="synthetic-model",
                    tier=ModelTier.FREE, limits=None, quota=None, retry=None, paid_authorized=False):
    creds = tuple(credentials or (reference("primary", "PRIMARY"),))
    route = ModelRoute(provider_id=provider, model_id=model, tier=tier,
        supported_tasks={"analysis", "review"}, supports_structured_output=True)
    return PoolConfiguration(pool_id=pool_id, enabled=True, routes=(PoolRoute(route=route, credentials=creds),),
        limits=limits or pool_limits(), quota_limits=quota or quota_limits(),
        retry=retry or PoolRetryPolicy(max_retries_per_credential=0, initial_backoff_seconds=0.25,
            max_backoff_seconds=5, max_retry_after_seconds=20, jitter_fraction=0), paid_authorized=paid_authorized)


def configs(*, a=None, b=None, c=None):
    return PoolConfigurations(pools={PoolId.POOL_A: a or PoolConfiguration(pool_id=PoolId.POOL_A),
        PoolId.POOL_B: b or PoolConfiguration(pool_id=PoolId.POOL_B),
        PoolId.POOL_C: c or PoolConfiguration(pool_id=PoolId.POOL_C)})


def request(agent="code_quality_analyst", *, pool=None, idempotent=True, output_tokens=8, **kw):
    values = dict(agent_id=agent, pool_id=pool, execution_id=str(uuid.uuid4()),
        messages=(LLMMessage(role="user", content="synthetic prompt"),), correlation_id="corr", trace_id="trace",
        task="analysis", max_output_tokens=output_tokens, idempotent=idempotent)
    values.update(kw)
    return GatewayRequest(**values)


def gateway(configuration, provider, secrets, *, policy=None, sleeper=None, random_value=lambda: 0.0):
    args = dict(policy=policy or GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE},
            limits=RequestLimits(max_output_tokens=16, max_concurrent_requests=8)),
        pool_configurations=configuration, credential_resolver=FakeResolver(secrets),
        providers={"synthetic": provider}, pool_random_value=random_value)
    if sleeper is not None: args["pool_sleeper"] = sleeper
    return LLMGateway(**args)


class CredentialPoolTests(SimpleTestCase):
    def test_exact_stable_agent_pool_mapping_and_agent_one_exclusion(self):
        self.assertEqual(AGENT_POOL_MAP, {
            "business_logic_analyst": PoolId.POOL_A, "code_quality_analyst": PoolId.POOL_A,
            "security_analyst": PoolId.POOL_B, "migration_planner": PoolId.POOL_B,
            "patch_author": PoolId.POOL_B, "test_engineer": PoolId.POOL_C,
            "review_agent": PoolId.POOL_C})
        provider = FakePoolProvider()
        active = configured_pool(credentials=(reference("a-primary", "A_PRIMARY"),))
        gate = gateway(configs(a=active), provider, {"CODEWISE_LLM_CREDENTIAL_A_PRIMARY": "synthetic-secret"})
        with self.assertRaises(PolicyDenied):
            gate.invoke(request(agent="structure_analyzer"), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls, [])

        legacy_route = ModelRoute(provider_id="synthetic", model_id="synthetic-model", tier=ModelTier.FREE)
        legacy = LLMGateway(policy=GatewayPolicy(enabled=True, allowed_agents={"structure_analyzer"},
                allowed_models={("synthetic", "synthetic-model")}, enabled_tiers={ModelTier.FREE}),
            routes=[legacy_route], providers={"synthetic": provider})
        with self.assertRaises(PolicyDenied):
            legacy.invoke(request(agent="structure_analyzer"), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls, [])

    def test_unknown_agent_and_cross_pool_requests_are_denied(self):
        a = configured_pool(credentials=(reference("a-primary", "A_PRIMARY"),))
        b = configured_pool(PoolId.POOL_B, credentials=(reference("b-primary", "B_PRIMARY", account="b"),))
        provider = FakePoolProvider()
        gate = gateway(configs(a=a, b=b), provider, {
            "CODEWISE_LLM_CREDENTIAL_A_PRIMARY": "a-secret", "CODEWISE_LLM_CREDENTIAL_B_PRIMARY": "b-secret"})
        with self.assertRaises(UnknownAgentPool): gate.invoke(request(agent="unknown_specialist"), permissions={Permission.LLM_INVOKE})
        with self.assertRaises(CrossPoolAccessDenied):
            gate.invoke(request(pool="pool_b"), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls, [])

    def test_route_task_and_capability_requirements_are_checked_before_credentials(self):
        active = configured_pool(credentials=(reference("primary", "PRIMARY"),))
        provider = FakePoolProvider()
        gate = gateway(configs(a=active), provider, {"CODEWISE_LLM_CREDENTIAL_PRIMARY": "synthetic-secret"})
        with self.assertRaises(PolicyDenied):
            gate.invoke(request(required_capabilities={"unsupported_capability"}),
                        permissions={Permission.LLM_INVOKE})
        with self.assertRaises(PolicyDenied):
            gate.invoke(request(task="unsupported_task"), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls, [])

    def test_all_pools_are_independently_configurable_and_disabled_by_default(self):
        disabled = PoolConfigurations.disabled()
        self.assertEqual(set(disabled.pools), set(PoolId))
        self.assertFalse(any(pool.enabled for pool in disabled.pools.values()))
        a = configured_pool(credentials=(reference("a", "A"),))
        b = configured_pool(PoolId.POOL_B, credentials=(reference("b", "B", account="b"),))
        c = configured_pool(PoolId.POOL_C, credentials=(reference("c", "C", account="c"),))
        combined = configs(a=a, b=b, c=c)
        self.assertTrue(all(combined.pools[key].enabled for key in PoolId))
        self.assertEqual(combined.pools[PoolId.POOL_A].routes[0].primary.credential_id, "a")
        self.assertEqual(combined.pools[PoolId.POOL_B].routes[0].primary.credential_id, "b")
        self.assertEqual(combined.pools[PoolId.POOL_C].routes[0].primary.credential_id, "c")

    def test_primary_then_multiple_fallbacks_and_retry_after(self):
        credentials = (reference("primary", "PRIMARY", quota="q1"),
            reference("fallback-one", "FALLBACK_ONE", account="a2", quota="q2"),
            reference("fallback-two", "FALLBACK_TWO", account="a3", quota="q3"))
        policy = PoolRetryPolicy(max_retries_per_credential=0, initial_backoff_seconds=.25,
            max_backoff_seconds=5, max_retry_after_seconds=10, jitter_fraction=0)
        active = configured_pool(credentials=credentials, retry=policy)
        provider = FakePoolProvider({
            "primary": [ProviderCallError(ProviderFailureKind.RATE_LIMIT, retry_after_seconds=2)],
            "fallback-one": [ProviderCallError(ProviderFailureKind.NETWORK_RETRYABLE)],})
        waits = []
        gate = gateway(configs(a=active), provider, {f"CODEWISE_LLM_CREDENTIAL_{name}": f"secret-{name}"
            for name in ("PRIMARY", "FALLBACK_ONE", "FALLBACK_TWO")}, sleeper=waits.append)
        result = gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual([call[2] for call in provider.calls], ["primary", "fallback-one", "fallback-two"])
        self.assertEqual(waits, [2, .25])
        self.assertEqual(result.pool_id, PoolId.POOL_A.value)

    def test_same_quota_keys_are_not_treated_as_independent_capacity(self):
        credentials = (reference("primary", "PRIMARY", account="shared", quota="same"),
            reference("same-account-key", "SAME_ACCOUNT", account="shared", quota="same"),
            reference("other-account", "OTHER_ACCOUNT", account="other", quota="other"))
        provider = FakePoolProvider({"primary": [ProviderCallError(ProviderFailureKind.QUOTA_EXHAUSTED)]})
        gate = gateway(configs(a=configured_pool(credentials=credentials)), provider,
            {f"CODEWISE_LLM_CREDENTIAL_{name}": "synthetic" for name in ("PRIMARY", "SAME_ACCOUNT", "OTHER_ACCOUNT")},
            sleeper=lambda delay: None)
        gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual([call[2] for call in provider.calls], ["primary", "other-account"])

    def test_distinct_quota_identities_are_independently_available(self):
        credentials = (reference("primary", "PRIMARY", account="acct-1", project="p1", quota="q1"),
            reference("secondary", "SECONDARY", account="acct-2", project="p2", quota="q2"))
        provider = FakePoolProvider({"primary": [ProviderCallError(ProviderFailureKind.RATE_LIMIT)]})
        gate = gateway(configs(a=configured_pool(credentials=credentials)), provider,
            {"CODEWISE_LLM_CREDENTIAL_PRIMARY": "secret-1", "CODEWISE_LLM_CREDENTIAL_SECONDARY": "secret-2"},
            sleeper=lambda delay: None)
        result = gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(result.provider_id, "synthetic")
        self.assertEqual([call[2] for call in provider.calls], ["primary", "secondary"])

    def test_ordered_provider_model_routes_fallback_only_within_the_agent_pool(self):
        first_ref = reference("route-one-primary", "ROUTE_ONE", provider="synthetic-one", quota="one")
        second_ref = reference("route-two-primary", "ROUTE_TWO", provider="synthetic-two", account="account-2", quota="two")
        first_route = ModelRoute(provider_id="synthetic-one", model_id="model-one", tier=ModelTier.FREE,
                                 supported_tasks={"analysis"})
        second_route = ModelRoute(provider_id="synthetic-two", model_id="model-two", tier=ModelTier.FREE,
                                  supported_tasks={"analysis"})
        active = PoolConfiguration(pool_id=PoolId.POOL_A, enabled=True,
            routes=(PoolRoute(route=first_route, credentials=(first_ref,)),
                    PoolRoute(route=second_route, credentials=(second_ref,))),
            limits=pool_limits(), quota_limits=quota_limits(),
            retry=PoolRetryPolicy(max_retries_per_credential=0, circuit_breaker_failures=1))
        first = FakePoolProvider({"route-one-primary": [ProviderCallError(ProviderFailureKind.TEMPORARY_OUTAGE)]})
        second = FakePoolProvider()
        gate = LLMGateway(policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE},
                limits=RequestLimits(max_output_tokens=16, max_concurrent_requests=8)),
            pool_configurations=configs(a=active), credential_resolver=FakeResolver({
                "CODEWISE_LLM_CREDENTIAL_ROUTE_ONE": "synthetic-secret-one",
                "CODEWISE_LLM_CREDENTIAL_ROUTE_TWO": "synthetic-secret-two"}),
            providers={"synthetic-one": first, "synthetic-two": second}, pool_sleeper=lambda _: None)
        response = gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(response.model_id, "model-two")
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)

    def test_pool_concurrency_limit_is_enforced(self):
        active = configured_pool(credentials=(reference("primary", "PRIMARY"),),
                                 limits=pool_limits(max_concurrent_requests=1))
        provider = FakePoolProvider()
        provider.release = threading.Event()
        gate = gateway(configs(a=active), provider, {"CODEWISE_LLM_CREDENTIAL_PRIMARY": "synthetic-secret"})
        errors = []
        thread = threading.Thread(target=lambda: self._invoke(gate, request(), errors))
        thread.start()
        self.assertTrue(provider.entered.wait(1))
        with self.assertRaises(RateLimited):
            gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        provider.release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_noneligible_failures_never_fallback(self):
        credentials = (reference("primary", "PRIMARY", quota="q1"),
            reference("fallback", "FALLBACK", account="acct-2", quota="q2"))
        for kind in (ProviderFailureKind.AUTHENTICATION, ProviderFailureKind.INVALID_REQUEST,
                     ProviderFailureKind.UNSUPPORTED_MODEL, ProviderFailureKind.UNSUPPORTED_CAPABILITY,
                     ProviderFailureKind.STRUCTURED_OUTPUT_INVALID, ProviderFailureKind.SAFETY_REFUSAL,
                     ProviderFailureKind.PERMANENT):
            with self.subTest(kind=kind):
                provider = FakePoolProvider({"primary": [ProviderCallError(kind)]})
                gate = gateway(configs(a=configured_pool(credentials=credentials)), provider,
                    {"CODEWISE_LLM_CREDENTIAL_PRIMARY": "synthetic", "CODEWISE_LLM_CREDENTIAL_FALLBACK": "synthetic"})
                with self.assertRaises(ProviderRejected):
                    gate.invoke(request(), permissions={Permission.LLM_INVOKE})
                self.assertEqual([call[2] for call in provider.calls], ["primary"])

    def test_invalid_structured_output_does_not_trigger_fallback(self):
        credentials = (reference("primary", "PRIMARY", quota="q1"),
            reference("fallback", "FALLBACK", account="acct-2", quota="q2"))
        provider = FakePoolProvider({"primary": [lambda route, req, cred:
            ProviderResponse(content={"wrong": True})]})
        gate = gateway(configs(a=configured_pool(credentials=credentials)), provider,
            {"CODEWISE_LLM_CREDENTIAL_PRIMARY": "synthetic", "CODEWISE_LLM_CREDENTIAL_FALLBACK": "synthetic"})
        from llm_gateway.errors import InvalidProviderResponse
        with self.assertRaises(InvalidProviderResponse):
            gate.invoke(request(output_schema={"type": "object", "required": ["ok"]}),
                        permissions={Permission.LLM_INVOKE})
        self.assertEqual([call[2] for call in provider.calls], ["primary"])

    def test_retry_after_beyond_safe_bound_stops_fallback(self):
        credentials = (reference("primary", "PRIMARY", quota="q1"),
            reference("fallback", "FALLBACK", account="acct-2", quota="q2"))
        provider = FakePoolProvider({"primary": [ProviderCallError(ProviderFailureKind.RATE_LIMIT, retry_after_seconds=30)]})
        gate = gateway(configs(a=configured_pool(credentials=credentials)), provider,
            {"CODEWISE_LLM_CREDENTIAL_PRIMARY": "synthetic", "CODEWISE_LLM_CREDENTIAL_FALLBACK": "synthetic"})
        with self.assertRaises(PoolExhausted): gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual([call[2] for call in provider.calls], ["primary"])

    def test_shared_quota_concurrency_is_atomic_across_pools(self):
        shared_a = reference("pool-a", "A", account="shared", project="project", quota="shared")
        shared_c = reference("pool-c", "C", account="different-account", project="different-project", quota="shared")
        q_limits = quota_limits(max_concurrent_requests=1)
        a = configured_pool(credentials=(shared_a,), limits=pool_limits(max_concurrent_requests=2), quota=q_limits)
        c = configured_pool(PoolId.POOL_C, credentials=(shared_c,), limits=pool_limits(max_concurrent_requests=2), quota=q_limits)
        provider = FakePoolProvider()
        provider.release = threading.Event()
        gate = gateway(configs(a=a, c=c), provider, {"CODEWISE_LLM_CREDENTIAL_A": "a-secret",
            "CODEWISE_LLM_CREDENTIAL_C": "c-secret"})
        errors = []
        thread = threading.Thread(target=lambda: self._invoke(gate, request(), errors))
        thread.start()
        self.assertTrue(provider.entered.wait(1))
        with self.assertRaises(PoolExhausted):
            gate.invoke(request(agent="test_engineer"), permissions={Permission.LLM_INVOKE})
        provider.release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(provider.calls), 1)

    def test_pool_budget_exhaustion_does_not_borrow_another_pool(self):
        a = configured_pool(credentials=(reference("a", "A"),), limits=pool_limits(max_requests_per_minute=1))
        b = configured_pool(PoolId.POOL_B, credentials=(reference("b", "B", account="b"),))
        provider = FakePoolProvider()
        gate = gateway(configs(a=a, b=b), provider, {"CODEWISE_LLM_CREDENTIAL_A": "a-secret",
            "CODEWISE_LLM_CREDENTIAL_B": "b-secret"})
        gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        with self.assertRaises(PoolBudgetExceeded): gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        result = gate.invoke(request(agent="security_analyst"), permissions={Permission.LLM_INVOKE})
        self.assertEqual(result.pool_id, PoolId.POOL_B.value)
        self.assertEqual([call[2] for call in provider.calls], ["a", "b"])

    def test_environment_credentials_are_references_and_missing_values_fail_closed(self):
        ref = reference("env-credential", "LOCAL")
        resolver = EnvironmentCredentialResolver({"CODEWISE_LLM_CREDENTIAL_LOCAL": "synthetic-key-only"})
        resolved = resolver.resolve(ref)
        self.assertEqual(resolved.value.get_secret_value(), "synthetic-key-only")
        self.assertNotIn("synthetic-key-only", repr(resolved))
        with self.assertRaises(CredentialResolutionError):
            EnvironmentCredentialResolver({}).resolve(ref)
        with self.assertRaises(Exception) as caught:
            gateway(configs(a=configured_pool(credentials=(ref,))), FakePoolProvider(), {})
        self.assertNotIn("synthetic-key-only", str(caught.exception))

    def test_resolved_secret_is_redacted_from_provider_output_and_observer(self):
        secret = "synthetic-secret-value"
        credential = reference("primary", "PRIMARY")
        active = configured_pool(credentials=(credential,))
        class EchoProvider(FakePoolProvider):
            def complete(self, route, request, *, timeout_seconds, credential):
                self.calls.append((route.provider_id, route.model_id, credential.credential_id))
                return ProviderResponse(content={"ok": True, "echo": secret}, usage=Usage(input_tokens=2, output_tokens=2))
        events = []
        gate = LLMGateway(policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE},
                limits=RequestLimits(max_output_tokens=16, max_concurrent_requests=8)),
            pool_configurations=configs(a=active),
            credential_resolver=FakeResolver({"CODEWISE_LLM_CREDENTIAL_PRIMARY": secret}),
            providers={"synthetic": EchoProvider()}, observer=events.append)
        result = gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(result.output["echo"], "[REDACTED]")
        self.assertNotIn(secret, str(events))
        self.assertNotIn(secret, str(result))

    def test_same_account_project_cannot_be_declared_as_two_quota_buckets(self):
        first = configured_pool(credentials=(reference("one", "ONE", account="same", project="same-project", quota="q1"),))
        second = configured_pool(PoolId.POOL_B,
            credentials=(reference("two", "TWO", account="same", project="same-project", quota="q2"),))
        with self.assertRaises(ValueError): configs(a=first, b=second)

    def test_gateway_environment_factory_loads_refs_and_env_secrets(self):
        active = configured_pool(credentials=(reference("primary", "FROM_FACTORY"),))
        values = {"CODEWISE_LLM_POOL_A_CONFIG": json.dumps(active.model_dump(mode="json", exclude={"pool_id"})),
            "CODEWISE_LLM_CREDENTIAL_FROM_FACTORY": "synthetic-env-secret"}
        gate = LLMGateway.from_environment(providers={"synthetic": FakePoolProvider()},
            policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE},
                limits=RequestLimits(max_output_tokens=16, max_concurrent_requests=8)), environ=values)
        result = gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertTrue(result.output["ok"])

    def test_environment_pool_configuration_contains_refs_not_values(self):
        config = configured_pool(credentials=(reference("only-ref", "CONFIG"),))
        raw = config.model_dump(mode="json", exclude={"pool_id"})
        loaded = PoolConfigurations.from_environment({
            "CODEWISE_LLM_POOL_A_CONFIG": json.dumps(raw),
            "CODEWISE_LLM_POOL_B_CONFIG": "", "CODEWISE_LLM_POOL_C_CONFIG": ""})
        self.assertEqual(loaded.pools[PoolId.POOL_A].routes[0].primary.secret_ref,
                         "CODEWISE_LLM_CREDENTIAL_CONFIG")

    def test_paid_routes_require_both_pool_and_gateway_authorization(self):
        paid = configured_pool(credentials=(reference("paid", "PAID"),), tier=ModelTier.PAID,
                               paid_authorized=True)
        provider = FakePoolProvider()
        gate = gateway(configs(a=paid), provider, {"CODEWISE_LLM_CREDENTIAL_PAID": "synthetic"},
            policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.PAID}, allow_paid=False,
                limits=RequestLimits(max_output_tokens=16, max_concurrent_requests=8)))
        with self.assertRaises(PolicyDenied): gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls, [])

    def test_circuit_breaker_and_request_pool_are_isolated(self):
        credentials = (reference("a-primary", "A", quota="q1"),
            reference("a-secondary", "A2", account="a2", quota="q2"))
        a = configured_pool(credentials=credentials, retry=PoolRetryPolicy(max_retries_per_credential=0,
            circuit_breaker_failures=1, circuit_breaker_cooldown_seconds=30))
        b = configured_pool(PoolId.POOL_B, credentials=(reference("b-primary", "B", account="b"),),
            retry=PoolRetryPolicy(max_retries_per_credential=0, circuit_breaker_failures=1))
        provider = FakePoolProvider({"a-primary": [ProviderCallError(ProviderFailureKind.TEMPORARY_OUTAGE)]})
        gate = gateway(configs(a=a, b=b), provider, {"CODEWISE_LLM_CREDENTIAL_A": "a1",
            "CODEWISE_LLM_CREDENTIAL_A2": "a2", "CODEWISE_LLM_CREDENTIAL_B": "b"}, sleeper=lambda _: None)
        with self.assertRaises(PoolExhausted): gate.invoke(request(), permissions={Permission.LLM_INVOKE})
        self.assertEqual(len(provider.calls), 1)
        gate.invoke(request(agent="security_analyst"), permissions={Permission.LLM_INVOKE})
        self.assertEqual(provider.calls[-1][2], "b-primary")

    def test_agent_definition_rejects_both_llm_flag_and_permission(self):
        class Input(BaseModel): pass
        class Output(BaseModel): pass
        for kwargs in ({"requires_llm": True}, {"required_permissions": {Permission.LLM_INVOKE}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AgentDefinition.create(agent_id="structure_analyzer", display_name="Structure", description="test",
                    version="1", input_model=Input, output_model=Output, runner=lambda value, context: {}, **kwargs)

    @staticmethod
    def _invoke(gateway_instance, request_value, errors):
        try: gateway_instance.invoke(request_value, permissions={Permission.LLM_INVOKE})
        except Exception as exc: errors.append(exc)
