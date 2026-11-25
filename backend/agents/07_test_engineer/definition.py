"""Explicit Pool C Agent Foundation definition for test planning."""

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry

from .agent import TestEngineer
from .schemas.input import TestEngineerInput
from .schemas.output import TestEngineerOutput
from .services.analysis_service import AGENT_ID, AGENT_VERSION


def create_definition(repository_reader) -> AgentDefinition:
    agent = TestEngineer(repository_reader)
    return AgentDefinition.create(agent_id=AGENT_ID, display_name="Test Engineer",
        description="Inventories tests statically and produces evidence-backed test plans without executing code.",
        version=AGENT_VERSION, input_model=TestEngineerInput, output_model=TestEngineerOutput,
        runner=agent.run, capabilities={AgentCapability.DETERMINISTIC, AgentCapability.LLM_REASONING,
                                        AgentCapability.REPOSITORY_READ},
        required_permissions={Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ, Permission.LLM_INVOKE},
        requires_llm=True, supported_modes=("inline", "graph"),
        metadata={"specialist": True, "pool_id": "pool_c", "static_test_inventory": True,
                  "test_execution": False, "patch_application": False})


def register(registry: AgentRegistry, repository_reader) -> AgentDefinition:
    definition = create_definition(repository_reader)
    registry.register(definition)
    return definition
