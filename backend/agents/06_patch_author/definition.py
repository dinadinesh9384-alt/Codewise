"""Explicit Pool B Agent Foundation definition for reviewable patch proposals."""

from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry

from .agent import PatchAuthor
from .schemas.input import PatchAuthorInput
from .schemas.output import PatchAuthorOutput
from .services.analysis_service import AGENT_ID, AGENT_VERSION


def create_definition(repository_reader) -> AgentDefinition:
    agent = PatchAuthor(repository_reader)
    return AgentDefinition.create(agent_id=AGENT_ID, display_name="Patch Author",
        description="Creates evidence-backed, deterministically validated patch proposals for human review; never applies changes.",
        version=AGENT_VERSION, input_model=PatchAuthorInput, output_model=PatchAuthorOutput,
        runner=agent.run, capabilities={AgentCapability.DETERMINISTIC, AgentCapability.LLM_REASONING,
                                        AgentCapability.REPOSITORY_READ},
        required_permissions={Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ, Permission.LLM_INVOKE,
                              Permission.PATCH_PROPOSE},
        requires_llm=True, supported_modes=("inline", "graph"),
        metadata={"specialist": True, "pool_id": "pool_b", "deterministic_patch_validation": True,
                  "patch_application": False})


def register(registry: AgentRegistry, repository_reader) -> AgentDefinition:
    definition = create_definition(repository_reader)
    registry.register(definition)
    return definition
