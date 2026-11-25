"""Explicit Pool C Agent Foundation definition for the independent final reviewer."""

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry

from .agent import ReviewAgent
from .schemas.input import ReviewAgentInput
from .schemas.output import ReviewAgentOutput
from .services.review_service import AGENT_ID, AGENT_VERSION


def create_definition(repository_reader) -> AgentDefinition:
    agent = ReviewAgent(repository_reader)
    return AgentDefinition.create(agent_id=AGENT_ID, display_name="Review Agent",
        description="Independently reviews bounded upstream analysis and evidence; never approves or applies changes.",
        version=AGENT_VERSION, input_model=ReviewAgentInput, output_model=ReviewAgentOutput,
        runner=agent.run, capabilities={AgentCapability.DETERMINISTIC, AgentCapability.LLM_REASONING,
                                        AgentCapability.REPOSITORY_READ},
        required_permissions={Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ, Permission.LLM_INVOKE},
        requires_llm=True, supported_modes=("inline", "graph"),
        metadata={"specialist": True, "pool_id": "pool_c", "independent_review": True,
                  "patch_application": False, "test_execution": False, "publishing": False})


def register(registry: AgentRegistry, repository_reader) -> AgentDefinition:
    definition = create_definition(repository_reader)
    registry.register(definition)
    return definition
