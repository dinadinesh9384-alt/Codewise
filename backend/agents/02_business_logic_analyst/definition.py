"""Explicit Pool A Agent Foundation definition for the business logic analyst."""

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry

from .agent import BusinessLogicAnalyst
from .schemas.input import BusinessAnalysisInput
from .schemas.output import BusinessAnalysisOutput
from .services.analysis_service import AGENT_ID, AGENT_VERSION


def create_definition(repository_reader) -> AgentDefinition:
    agent = BusinessLogicAnalyst(repository_reader)
    return AgentDefinition.create(agent_id=AGENT_ID, display_name="Business Logic Analyst",
        description="Analyzes business workflows, rules, entities, and cross-file execution paths from a sealed repository snapshot.",
        version=AGENT_VERSION, input_model=BusinessAnalysisInput, output_model=BusinessAnalysisOutput,
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
