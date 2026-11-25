"""Explicit Pool B Agent Foundation definition for migration planning."""

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry

from .agent import MigrationPlanner
from .schemas.input import MigrationAnalysisInput
from .schemas.output import MigrationPlanOutput
from .services.analysis_service import AGENT_ID, AGENT_VERSION


def create_definition(repository_reader) -> AgentDefinition:
    agent = MigrationPlanner(repository_reader)
    return AgentDefinition.create(agent_id=AGENT_ID, display_name="Migration Planner",
        description="Builds evidence-backed, dependency-aware migration plans from immutable repository snapshots.",
        version=AGENT_VERSION, input_model=MigrationAnalysisInput, output_model=MigrationPlanOutput,
        runner=agent.run, capabilities={AgentCapability.DETERMINISTIC, AgentCapability.LLM_REASONING,
                                        AgentCapability.REPOSITORY_READ},
        required_permissions={Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ, Permission.LLM_INVOKE},
        requires_llm=True, supported_modes=("inline", "graph"),
        metadata={"specialist": True, "pool_id": "pool_b", "deterministic_preanalysis": True})


def register(registry: AgentRegistry, repository_reader) -> AgentDefinition:
    definition = create_definition(repository_reader)
    registry.register(definition)
    return definition
