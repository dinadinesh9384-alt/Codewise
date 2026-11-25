import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from agent_foundation.permissions import Permission
from config.workflow_runtime import (
    WorkflowRuntimeConfigurationError,
    CodeWiseWorkflowRuntime,
    build_gateway_from_environment,
    create_gateway_chat_model,
    load_runtime_configuration,
    open_checkpoint_pool,
)
from llm_gateway.pool_contracts import AGENT_POOL_MAP, PoolId
from llm_gateway.pool_contracts import PoolConfigurations
from llm_gateway.errors import PoolConfigurationError
from orchestration.checkpoint_capability import CheckpointCapability


def base_environment():
    return {
        "CODEWISE_LLM_POLICY_CONFIG": json.dumps({
            "enabled": False, "allowed_agents": [], "allowed_models": [],
            "enabled_tiers": [], "allow_paid": False,
        }),
        "CODEWISE_LLM_MODEL_CAPABILITIES": json.dumps({"groq": {}, "gemini": {}}),
    }


class RuntimeConfigurationTests(SimpleTestCase):
    def test_global_policy_and_all_three_disabled_pools_load_explicitly(self):
        policy, pools, providers = load_runtime_configuration(base_environment())
        self.assertFalse(policy.enabled)
        self.assertFalse(policy.allow_paid)
        self.assertEqual(set(pools.pools), set(PoolId))
        self.assertEqual(set(providers), {"groq", "gemini"})
        self.assertFalse(any(pool.enabled for pool in pools.pools.values()))
        self.assertIsNone(providers["gemini"].api_version)

    def test_gemini_api_version_can_be_explicitly_selected_and_is_validated(self):
        environ = base_environment()
        environ["CODEWISE_GEMINI_API_VERSION"] = "v1beta"
        _policy, _pools, providers = load_runtime_configuration(environ)
        self.assertEqual(providers["gemini"].api_version, "v1beta")
        environ["CODEWISE_GEMINI_API_VERSION"] = "beta"
        with self.assertRaises(WorkflowRuntimeConfigurationError):
            load_runtime_configuration(environ)

    def test_missing_policy_and_unknown_model_feature_fail_closed(self):
        with self.assertRaises(WorkflowRuntimeConfigurationError):
            load_runtime_configuration({"CODEWISE_LLM_MODEL_CAPABILITIES": '{"groq":{},"gemini":{}}'})
        invalid = base_environment()
        invalid["CODEWISE_LLM_MODEL_CAPABILITIES"] = json.dumps({
            "groq": {"unverified-model": ["imaginary_feature"]}, "gemini": {}})
        with self.assertRaises(WorkflowRuntimeConfigurationError):
            load_runtime_configuration(invalid)

    def test_paid_policy_is_rejected_even_when_provider_credentials_could_exist(self):
        invalid = base_environment()
        invalid["CODEWISE_LLM_POLICY_CONFIG"] = json.dumps({
            "enabled": True, "allowed_agents": [], "allowed_models": [],
            "enabled_tiers": ["paid"], "allow_paid": True,
        })
        with self.assertRaises(WorkflowRuntimeConfigurationError):
            load_runtime_configuration(invalid)

    @staticmethod
    def active_pool_environment(*, include_credential=True):
        pool_agents = [agent for agent, pool_id in AGENT_POOL_MAP.items() if pool_id == PoolId.POOL_A]
        environ = {
            "CODEWISE_LLM_POLICY_CONFIG": json.dumps({
                "enabled": True, "allowed_agents": pool_agents,
                "allowed_models": [["groq", "verified-fixture-model"]],
                "enabled_tiers": ["free"], "allow_paid": False,
            }),
            "CODEWISE_LLM_MODEL_CAPABILITIES": json.dumps({
                "groq": {"verified-fixture-model": ["max_output_tokens", "system_messages"]},
                "gemini": {},
            }),
            "CODEWISE_LLM_POOL_A_CONFIG": json.dumps({
                "enabled": True,
                "routes": [{
                    "route": {"provider_id": "groq", "model_id": "verified-fixture-model",
                        "tier": "free", "supported_tasks": ["analysis"],
                        "capabilities": ["max_output_tokens", "system_messages"],
                        "supports_structured_output": False},
                    "credentials": [{"credential_id": "pool-a-primary",
                        "secret_ref": "CODEWISE_LLM_CREDENTIAL_POOL_A_PRIMARY",
                        "identity": {"provider_id": "groq", "account_id": "account-a",
                            "project_id": "project-a", "quota_bucket_id": "quota-a"}}],
                }],
                "limits": {"max_requests_per_minute": 10, "max_requests_per_execution": 8,
                    "max_requests_per_agent_per_hour": 100, "max_input_bytes_per_request": 10000,
                    "max_output_tokens_per_request": 512, "max_input_tokens_per_hour": 10000,
                    "max_output_tokens_per_hour": 10000, "max_concurrent_requests": 2},
                "quota_limits": {"max_requests_per_minute": 10, "max_input_tokens_per_hour": 10000,
                    "max_output_tokens_per_hour": 10000, "max_concurrent_requests": 2},
            }),
        }
        if include_credential:
            environ["CODEWISE_LLM_CREDENTIAL_POOL_A_PRIMARY"] = "offline-fixture-secret"
        return environ

    def test_enabled_route_loads_verified_pool_and_secret_stays_out_of_route_config(self):
        gateway, pools, _resolver = build_gateway_from_environment(self.active_pool_environment())
        self.assertIsNotNone(gateway)
        route = pools.pools[PoolId.POOL_A].routes[0]
        self.assertEqual(route.route.model_id, "verified-fixture-model")
        self.assertNotIn("offline-fixture-secret", repr(pools))

    def test_missing_pool_credential_fails_gateway_construction(self):
        with self.assertRaises(PoolConfigurationError) as caught:
            build_gateway_from_environment(self.active_pool_environment(include_credential=False))
        self.assertNotIn("offline-fixture-secret", str(caught.exception))

    def test_gateway_models_are_bound_only_to_the_fixed_pool_for_agents_two_to_eight(self):
        request_id = uuid.uuid4()
        request = SimpleNamespace(execution_id=request_id, correlation_id="corr", trace_id="trace")
        for agent_id, pool in AGENT_POOL_MAP.items():
            with self.subTest(agent=agent_id):
                definition = SimpleNamespace(contract=SimpleNamespace(agent_id=agent_id))
                context = SimpleNamespace(agent_id=agent_id, execution_id=request_id,
                    permissions=frozenset({Permission.LLM_INVOKE}))
                model = create_gateway_chat_model(gateway=object(), definition=definition,
                                                  request=request, context=context)
                self.assertEqual(model.agent_id, agent_id)
                self.assertEqual(model.pool_id, pool.value)
                self.assertFalse(model.idempotent)

    def test_agent_one_and_mismatched_context_cannot_receive_a_model(self):
        request_id = uuid.uuid4()
        request = SimpleNamespace(execution_id=request_id, correlation_id="corr", trace_id="trace")
        for agent_id, context_agent in (("structure_analyzer", "structure_analyzer"),
                                        ("business_logic_analyst", "security_analyst")):
            definition = SimpleNamespace(contract=SimpleNamespace(agent_id=agent_id))
            context = SimpleNamespace(agent_id=context_agent, execution_id=request_id,
                permissions=frozenset({Permission.LLM_INVOKE}))
            with self.subTest(agent=agent_id), self.assertRaises(WorkflowRuntimeConfigurationError):
                create_gateway_chat_model(gateway=object(), definition=definition,
                                          request=request, context=context)

    def test_agents_without_llm_permission_cannot_receive_a_model(self):
        agent_id = "test_engineer"
        request_id = uuid.uuid4()
        definition = SimpleNamespace(contract=SimpleNamespace(agent_id=agent_id))
        context = SimpleNamespace(agent_id=agent_id, execution_id=request_id, permissions=frozenset())
        request = SimpleNamespace(execution_id=request_id, correlation_id="corr", trace_id="trace")
        with self.assertRaises(WorkflowRuntimeConfigurationError):
            create_gateway_chat_model(gateway=object(), definition=definition,
                                      request=request, context=context)

    def test_runtime_registers_all_eight_agents_and_leaves_approval_closed_without_checkpoint(self):
        workspace_id, repository_id, snapshot_id, execution_id = (uuid.uuid4() for _ in range(4))
        digest = "a" * 64
        commit = "b" * 40
        snapshot = SimpleNamespace(id=snapshot_id, workspace_id=workspace_id,
            repository_id=repository_id, commit_sha=commit, manifest_digest=digest)
        binding = SimpleNamespace(workspace_id=workspace_id, repository_id=repository_id,
            snapshot_id=snapshot_id, commit_sha=commit, manifest_digest=digest)
        user = SimpleNamespace(is_active=True)
        execution = SimpleNamespace(workspace_id=workspace_id, binding=binding,
            requested_by=user, requested_by_id="fixture-user")
        runtime = CodeWiseWorkflowRuntime(gateway=object(),
            pool_configurations=PoolConfigurations.disabled(), credential_resolver=object())
        workflow = runtime.workflow_for(execution=execution, snapshot=snapshot)
        self.assertTrue(workflow.can_produce_patch)
        self.assertTrue(workflow.can_approve)
        self.assertFalse(workflow.has_checkpointer)
        self.assertFalse(workflow.durable_checkpointer)

    def test_api_runtime_factory_configuration_errors_fail_closed_without_details(self):
        from django.test import override_settings
        from api.v1.services import _configured_runtime

        with patch.dict(os.environ, {}, clear=True), override_settings(
                CODEWISE_API_WORKFLOW_FACTORY="config.workflow_runtime.build_api_workflow_runtime"):
            self.assertIsNone(_configured_runtime())

    def test_checkpoint_storage_is_opt_in_and_requires_postgresql(self):
        from django.test import override_settings

        with override_settings(CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED=False):
            self.assertEqual(open_checkpoint_pool(), (None, None, None))
        with override_settings(CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED=True,
                               DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3"}}):
            with self.assertRaises(WorkflowRuntimeConfigurationError):
                open_checkpoint_pool()

    def test_checkpoint_capability_is_bound_to_saver_and_runtime(self):
        saver = type("PostgresSaver", (), {"__module__": "langgraph.checkpoint.postgres"})()
        runtime_id = uuid.uuid4()
        capability = CheckpointCapability(runtime_id=runtime_id, checkpointer=saver,
            database_engine="django.db.backends.postgresql",
            saver_kind="langgraph.checkpoint.postgres.PostgresSaver",
            dependencies_verified=True, schema_verified=True)
        self.assertTrue(capability.authorizes(checkpointer=saver, runtime_id=runtime_id))
        self.assertFalse(capability.authorizes(checkpointer=object(), runtime_id=runtime_id))
        self.assertFalse(capability.authorizes(checkpointer=saver, runtime_id=uuid.uuid4()))
        with self.assertRaises(ValueError):
            CheckpointCapability(runtime_id=runtime_id, checkpointer=saver,
                database_engine="django.db.backends.sqlite3",
                saver_kind="langgraph.checkpoint.postgres.PostgresSaver",
                dependencies_verified=True, schema_verified=True)
        with self.assertRaises(ValueError):
            CheckpointCapability(runtime_id=runtime_id, checkpointer=saver,
                database_engine="django.db.backends.postgresql", saver_kind="memory",
                dependencies_verified=True, schema_verified=True)

    def test_in_memory_saver_cannot_enable_workflow_durability(self):
        from orchestration.graph import in_memory_checkpointer_for_tests

        runtime_id = uuid.uuid4()
        saver = in_memory_checkpointer_for_tests()
        capability = CheckpointCapability(runtime_id=runtime_id, checkpointer=saver,
            database_engine="django.db.backends.postgresql",
            saver_kind="langgraph.checkpoint.postgres.PostgresSaver",
            dependencies_verified=True, schema_verified=True)
        runtime = CodeWiseWorkflowRuntime(gateway=object(),
            pool_configurations=PoolConfigurations.disabled(), credential_resolver=object(),
            checkpointer=saver, checkpoint_capability=capability, runtime_id=runtime_id)
        self.assertFalse(runtime.durable_approval_supported)
