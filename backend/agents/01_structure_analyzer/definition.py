"""Explicit Agent Foundation definition for the deterministic structure analyzer."""

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry

from .agent import StructureAnalyzer
from .schemas.input import StructureAnalysisInput
from .schemas.output import StructureAnalysisOutput
from .services.analysis_service import AGENT_ID, AGENT_VERSION


def create_definition(repository_reader) -> AgentDefinition:
    agent = StructureAnalyzer(repository_reader)
    return AgentDefinition.create(agent_id=AGENT_ID, display_name="Structure Analyzer",
        description="Deterministically inventories a sealed repository snapshot and reports structural evidence.",
        version=AGENT_VERSION, input_model=StructureAnalysisInput, output_model=StructureAnalysisOutput,
        runner=agent.run, capabilities={AgentCapability.DETERMINISTIC, AgentCapability.REPOSITORY_READ},
        required_permissions={Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ}, requires_llm=False,
        supported_modes=("inline", "graph"),
        metadata={"specialist": True, "deterministic": True})


def register(registry: AgentRegistry, repository_reader) -> AgentDefinition:
    definition = create_definition(repository_reader)
    registry.register(definition)
    return definition
