"""Trusted, environment-configured composition for the authenticated workflow API."""

import atexit
import json
import logging
import os
import uuid
from functools import lru_cache
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_foundation.adapters.langchain_adapter import GatewayChatModel
from agent_foundation.context import ExecutionContext, SnapshotRepositoryReader
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from agents.registry import (
    business_logic_analyst_binding,
    code_quality_analyst_binding,
    migration_planner_binding,
    patch_author_binding,
    register_available_agents,
    review_agent_binding,
    security_analyst_binding,
    structure_analyzer_binding,
    test_engineer_binding,
)
from apps.repositories.providers.github.snapshot_adapter import GitHubManifestSnapshotAdapter
from apps.repositories.storage.local import LocalSnapshotStorage
from orchestration.contracts import OFFICIAL
from orchestration.graph import build_workflow
from orchestration.checkpoint_capability import CheckpointCapability
from llm_gateway.credentials import EnvironmentCredentialResolver
from llm_gateway.gateway import LLMGateway
from llm_gateway.policy import GatewayPolicy
from llm_gateway.pool_contracts import AGENT_POOL_MAP, PoolConfigurations
from llm_gateway.provider_registry import ProviderAdapterRegistry

logger = logging.getLogger("codewise.workflow_runtime")
_SUPPORTED_FEATURES = frozenset({"structured_output", "system_messages", "max_output_tokens",
                                 "temperature", "top_p", "seed", "stop_sequences"})
_GEMINI_API_VERSIONS = frozenset({"v1", "v1beta", "v1alpha"})


class WorkflowRuntimeConfigurationError(Exception):
    """Safe configuration failure. Never include input values or SDK exceptions."""


class _CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    groq: dict[str, frozenset[str]] = Field(default_factory=dict)
    gemini: dict[str, frozenset[str]] = Field(default_factory=dict)

    def validate_features(self):
        for provider_models in (self.groq, self.gemini):
            for model_id, features in provider_models.items():
                if not model_id.strip() or not features <= _SUPPORTED_FEATURES:
                    raise WorkflowRuntimeConfigurationError(
                        "Model capability manifest contains an unknown model or feature."
                    )


def _parse_json_env(environ: Mapping[str, str], name: str, *, required: bool):
    raw = environ.get(name, "")
    if not isinstance(raw, str) or not raw.strip():
        if required:
            raise WorkflowRuntimeConfigurationError(f"Required runtime setting {name} is missing.")
        return None
    if len(raw) > 1_000_000:
        raise WorkflowRuntimeConfigurationError(f"Runtime setting {name} exceeds its size limit.")
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise WorkflowRuntimeConfigurationError(f"Runtime setting {name} is invalid JSON.") from None


def load_runtime_configuration(environ: Mapping[str, str] | None = None):
    """Load strict, secret-reference-only configuration without embedding secret values."""
    environ = os.environ if environ is None else environ
    policy_data = _parse_json_env(environ, "CODEWISE_LLM_POLICY_CONFIG", required=True)
    try:
        policy = GatewayPolicy.model_validate(policy_data)
    except (ValidationError, TypeError):
        raise WorkflowRuntimeConfigurationError("Global LLM policy configuration is invalid.") from None
    if policy.allow_paid or any(tier.value == "paid" for tier in policy.enabled_tiers):
        raise WorkflowRuntimeConfigurationError("Paid LLM use is disabled by this runtime.")

    capability_data = _parse_json_env(environ, "CODEWISE_LLM_MODEL_CAPABILITIES", required=True)
    try:
        capabilities = _CapabilityManifest.model_validate(capability_data)
        capabilities.validate_features()
    except (ValidationError, TypeError):
        raise WorkflowRuntimeConfigurationError("Model capability manifest is invalid.") from None

    gemini_api_version = environ.get("CODEWISE_GEMINI_API_VERSION")
    if gemini_api_version is not None:
        gemini_api_version = gemini_api_version.strip()
        if gemini_api_version not in _GEMINI_API_VERSIONS:
            raise WorkflowRuntimeConfigurationError("Gemini API version is unsupported.")

    try:
        pools = PoolConfigurations.from_environment(environ)
    except (ValueError, ValidationError):
        raise WorkflowRuntimeConfigurationError("One or more LLM pool configurations are invalid.") from None
    if any(pool.paid_authorized or any(route.route.tier.value == "paid" for route in pool.routes)
           for pool in pools.pools.values()):
        raise WorkflowRuntimeConfigurationError("Paid routes are disabled by this runtime.")

    llm_agents = frozenset(AGENT_POOL_MAP)
    if not policy.allowed_agents <= llm_agents:
        raise WorkflowRuntimeConfigurationError("Global policy contains an unknown or LLM-free agent.")
    active_routes = [route.route for pool in pools.pools.values() if pool.enabled for route in pool.routes]
    active_model_pairs = {(route.provider_id, route.model_id) for route in active_routes}
    if not policy.allowed_models <= active_model_pairs:
        raise WorkflowRuntimeConfigurationError("Global policy allow-lists an inactive or unconfigured model route.")
    if not policy.enabled_tiers <= {route.tier for route in active_routes}:
        raise WorkflowRuntimeConfigurationError("Global policy enables a model tier with no active route.")
    any_pool_enabled = any(pool.enabled for pool in pools.pools.values())
    if any_pool_enabled and not policy.enabled:
        raise WorkflowRuntimeConfigurationError("Enabled pools require an enabled global gateway policy.")
    if policy.enabled and (not any_pool_enabled or not policy.allowed_models or not policy.allowed_agents):
        raise WorkflowRuntimeConfigurationError("Enabled global policy requires an enabled, allow-listed pool route.")

    providers = ProviderAdapterRegistry.groq_and_gemini(
        model_capabilities={"groq": capabilities.groq, "gemini": capabilities.gemini},
        gemini_api_version=gemini_api_version)
    for pool in pools.pools.values():
        for configured_route in pool.routes:
            adapter = providers.get(configured_route.route.provider_id)
            if adapter is None:
                raise WorkflowRuntimeConfigurationError("A configured route has no supported provider adapter.")
            try:
                adapter.validate_route(configured_route.route)
            except Exception:
                raise WorkflowRuntimeConfigurationError(
                    "A configured route failed model capability validation."
                ) from None
            if pool.enabled:
                if (configured_route.route.provider_id, configured_route.route.model_id) not in policy.allowed_models:
                    raise WorkflowRuntimeConfigurationError("An enabled route is not allow-listed by global policy.")
                if configured_route.route.tier not in policy.enabled_tiers:
                    raise WorkflowRuntimeConfigurationError("An enabled route uses a disabled model tier.")
                required_agents = {agent for agent, pool_id in AGENT_POOL_MAP.items()
                                   if pool_id == pool.pool_id}
                if not required_agents <= policy.allowed_agents:
                    raise WorkflowRuntimeConfigurationError(
                        "Global policy does not explicitly allow every agent assigned to an enabled pool."
                    )
    return policy, pools, providers


def build_gateway_from_environment(environ: Mapping[str, str] | None = None):
    """Construct the only authorized gateway and resolve no secret into route config."""
    environ = os.environ if environ is None else environ
    policy, pools, providers = load_runtime_configuration(environ)
    credentials = EnvironmentCredentialResolver(environ)
    gateway = LLMGateway(policy=policy, pool_configurations=pools,
                         credential_resolver=credentials, providers=providers)
    return gateway, pools, credentials


def open_checkpoint_pool(*, verify_schema=True, runtime_id=None):
    """Open a bounded Postgres pool from Django's configured database settings."""
    from django.conf import settings

    if verify_schema and not settings.CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED:
        return None, None, None
    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.postgresql":
        raise WorkflowRuntimeConfigurationError(
            "Durable LangGraph checkpoints require the configured PostgreSQL database."
        )
    pool = None
    try:
        from psycopg import conninfo
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.checkpoint.postgres import PostgresSaver

        options = database.get("OPTIONS", {})
        fields = {"dbname": database.get("NAME"), "user": database.get("USER"),
                  "password": database.get("PASSWORD"), "host": database.get("HOST"),
                  "port": database.get("PORT"), "sslmode": options.get("sslmode"),
                  "sslrootcert": options.get("sslrootcert")}
        dsn = conninfo.make_conninfo(**{key: value for key, value in fields.items() if value})
        pool = ConnectionPool(conninfo=dsn,
            kwargs={"autocommit": True, "row_factory": dict_row}, min_size=1, max_size=10,
            timeout=5, open=True)
        pool.wait(timeout=5)
        saver = PostgresSaver(pool, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
        if not isinstance(saver, PostgresSaver):
            raise TypeError("Unsupported checkpoint saver implementation.")
        if verify_schema:
            with pool.connection() as connection:
                for table in ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                    connection.execute(f"SELECT 1 FROM {table} LIMIT 0")
        capability = None
        if verify_schema:
            capability = CheckpointCapability(
                runtime_id=runtime_id or uuid.uuid4(), checkpointer=saver,
                database_engine=database["ENGINE"],
                saver_kind="langgraph.checkpoint.postgres.PostgresSaver",
                dependencies_verified=True,
                schema_verified=True)
        return pool, saver, capability
    except Exception:
        if pool is not None:
            try:
                pool.close()
            except Exception:
                pass
        raise WorkflowRuntimeConfigurationError(
            "PostgreSQL checkpoint storage is unavailable."
        ) from None


def create_gateway_chat_model(*, gateway, definition, request, context):
    """Create a gateway-only LangChain model with the agent's immutable pool assignment."""
    agent_id = definition.contract.agent_id
    if agent_id == "structure_analyzer" or agent_id not in AGENT_POOL_MAP:
        raise WorkflowRuntimeConfigurationError("This agent is not authorized for gateway model access.")
    expected_pool = AGENT_POOL_MAP[agent_id]
    if (context.agent_id != agent_id or context.execution_id != request.execution_id or
            Permission.LLM_INVOKE not in context.permissions):
        raise WorkflowRuntimeConfigurationError("Agent model context failed authorization checks.")
    return GatewayChatModel(
        gateway=gateway, permissions=context.permissions, agent_id=agent_id,
        pool_id=expected_pool.value, execution_id=str(request.execution_id),
        correlation_id=request.correlation_id, trace_id=request.trace_id)


class CodeWiseWorkflowRuntime:
    """Per-process gateway and saver composition; graph objects remain request-scoped."""

    def __init__(self, *, gateway, pool_configurations, credential_resolver,
                 checkpointer=None, checkpoint_pool=None, checkpoint_capability=None, runtime_id=None):
        self.gateway = gateway
        self.pool_configurations = pool_configurations
        self.credential_resolver = credential_resolver
        self.checkpointer = checkpointer
        self._checkpoint_pool = checkpoint_pool
        self.runtime_id = runtime_id or uuid.uuid4()
        self.checkpoint_capability = checkpoint_capability
        self.durable_approval_supported = bool(checkpoint_capability and checkpoint_capability.authorizes(
            checkpointer=checkpointer, runtime_id=self.runtime_id))

    def workflow_for(self, *, execution, snapshot, observer=None):
        binding = execution.binding
        if (execution.workspace_id != snapshot.workspace_id or
                binding.workspace_id != execution.workspace_id or
                binding.repository_id != snapshot.repository_id or
                binding.snapshot_id != snapshot.id or binding.commit_sha != snapshot.commit_sha):
            raise WorkflowRuntimeConfigurationError("Execution snapshot identity could not be verified.")
        user = execution.requested_by
        if user is None or not user.is_active:
            raise WorkflowRuntimeConfigurationError("Workflow execution principal is unavailable.")
        reader = SnapshotRepositoryReader(user=user, manifest_codec=GitHubManifestSnapshotAdapter(),
                                          storage=LocalSnapshotStorage())
        registry = AgentRegistry()
        register_available_agents(registry, reader)
        registry.seal()
        definitions = {agent.agent_id: agent for agent in registry.metadata()}
        if set(definitions) != set(OFFICIAL):
            raise WorkflowRuntimeConfigurationError("The registered workflow agent set is incomplete.")

        def context_factory(workflow_request, agent_id, agent_execution_id):
            definition = registry.resolve(agent_id)
            permissions = frozenset(Permission(value) for value in definition.contract.required_permissions)
            if agent_id == "structure_analyzer" and Permission.LLM_INVOKE in permissions:
                raise WorkflowRuntimeConfigurationError("Agent 1 cannot receive LLM access.")
            return ExecutionContext.make(
                principal_id=str(execution.requested_by_id), workspace_id=workflow_request.workspace_id,
                repository_id=workflow_request.repository_id, snapshot_id=workflow_request.snapshot_id,
                commit_sha=workflow_request.commit_sha, manifest_digest=workflow_request.manifest_digest,
                execution_id=agent_execution_id, agent_id=agent_id, permissions=permissions,
                correlation_id=workflow_request.correlation_id, trace_id=workflow_request.trace_id,
                deadline=workflow_request.deadline)

        def chat_model_factory(definition, request, context):
            return create_gateway_chat_model(gateway=self.gateway, definition=definition,
                                             request=request, context=context)

        bindings = {
            "structure_analyzer": structure_analyzer_binding(context_factory),
            "business_logic_analyst": business_logic_analyst_binding(context_factory),
            "code_quality_analyst": code_quality_analyst_binding(context_factory),
            "security_analyst": security_analyst_binding(context_factory),
            "migration_planner": migration_planner_binding(context_factory),
            "patch_author": patch_author_binding(context_factory),
            "test_engineer": test_engineer_binding(context_factory),
            "review_agent": review_agent_binding(context_factory),
        }
        lifecycle = ExecutionLifecycle(registry, chat_model_factory=chat_model_factory)
        return build_workflow(registry, lifecycle, bindings,
                              checkpointer=self.checkpointer,
                              checkpoint_capability=self.checkpoint_capability,
                              runtime_id=self.runtime_id, observer=observer)

    def close(self):
        if self._checkpoint_pool is not None:
            self._checkpoint_pool.close()


@lru_cache(maxsize=1)
def build_api_workflow_runtime():
    """Django ``CODEWISE_API_WORKFLOW_FACTORY`` target. Configuration fails closed."""
    try:
        gateway, pools, credentials = build_gateway_from_environment()
        runtime_id = uuid.uuid4()
        checkpoint_pool, checkpointer, checkpoint_capability = open_checkpoint_pool(runtime_id=runtime_id)
    except WorkflowRuntimeConfigurationError:
        raise
    except Exception:
        logger.error("Workflow runtime configuration failed validation.")
        raise WorkflowRuntimeConfigurationError("Workflow runtime configuration is unavailable.") from None
    runtime = CodeWiseWorkflowRuntime(gateway=gateway, pool_configurations=pools,
        credential_resolver=credentials, checkpointer=checkpointer, checkpoint_pool=checkpoint_pool,
        checkpoint_capability=checkpoint_capability, runtime_id=runtime_id)
    if checkpoint_pool is not None:
        atexit.register(runtime.close)
    return runtime
