"""Explicit Pool A Agent Foundation definition for code quality analysis."""

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry

from .agent import CodeQualityAnalyst
from .schemas.input import CodeQualityInput
from .schemas.output import CodeQualityOutput
from .services.analysis_service import AGENT_ID, AGENT_VERSION


def create_definition(repository_reader) -> AgentDefinition:
    agent = CodeQualityAnalyst(repository_reader)
    return AgentDefinition.create(agent_id=AGENT_ID, display_name="Code Quality Analyst",
        description="Measures code complexity, duplication, error handling, typing, coupling, and testability from a sealed repository snapshot.",
        version=AGENT_VERSION, input_model=CodeQualityInput, output_model=CodeQualityOutput,
        runner=agent.run, capabilities={AgentCapability.DETERMINISTIC, AgentCapability.LLM_REASONING,
                                        AgentCapability.REPOSITORY_READ},
        required_permissions={Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ, Permission.LLM_INVOKE},
        requires_llm=True, supported_modes=("inline", "graph"),
        metadata={"specialist": True, "pool_id": "pool_a", "deterministic_preanalysis": True,
                  "supported_languages": ["python"]})


def register(registry: AgentRegistry, repository_reader) -> AgentDefinition:
    definition = create_definition(repository_reader)
    registry.register(definition)
    return definition
