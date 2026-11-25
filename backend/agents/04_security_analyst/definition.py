"""Explicit Pool B Agent Foundation definition for repository security analysis."""

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry

from .agent import SecurityAnalyst
from .schemas.input import SecurityAnalysisInput
from .schemas.output import SecurityAnalysisOutput
from .services.analysis_service import AGENT_ID, AGENT_VERSION


def create_definition(repository_reader) -> AgentDefinition:
    agent = SecurityAnalyst(repository_reader)
    return AgentDefinition.create(agent_id=AGENT_ID, display_name="Security Analyst",
        description="Analyzes bounded repository snapshots for evidence-backed security risks and interprets them through the shared LLM gateway.",
        version=AGENT_VERSION, input_model=SecurityAnalysisInput, output_model=SecurityAnalysisOutput,
        runner=agent.run, capabilities={AgentCapability.DETERMINISTIC, AgentCapability.LLM_REASONING,
                                        AgentCapability.REPOSITORY_READ},
        required_permissions={Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ, Permission.LLM_INVOKE},
        requires_llm=True, supported_modes=("inline", "graph"),
        metadata={"specialist": True, "pool_id": "pool_b", "deterministic_preanalysis": True,
                  "supported_languages": ["python"]})


def register(registry: AgentRegistry, repository_reader) -> AgentDefinition:
    definition = create_definition(repository_reader)
    registry.register(definition)
    return definition
