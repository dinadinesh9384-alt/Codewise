"""Explicit, trusted registration of agent contracts and implementations."""

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from .contracts.agent import AgentCapability, AgentContract
from .errors import DuplicateAgentError, UnknownAgentError


@dataclass(frozen=True)
class AgentDefinition:
    contract: AgentContract
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    runner: Callable[[BaseModel, Any], Any]

    def __post_init__(self):
        if not isinstance(self.contract, AgentContract):
            raise TypeError("contract must be an AgentContract")
        if not isinstance(self.input_model, type) or not issubclass(self.input_model, BaseModel):
            raise TypeError("input_model must be a Pydantic model class")
        if not isinstance(self.output_model, type) or not issubclass(self.output_model, BaseModel):
            raise TypeError("output_model must be a Pydantic model class")
        if not callable(self.runner):
            raise TypeError("runner must be callable")
        if self.contract.agent_id == "structure_analyzer" and (
            self.contract.requires_llm or "llm.invoke" in self.contract.required_permissions
        ):
            raise ValueError("structure_analyzer cannot require or receive LLM access")

    @classmethod
    def create(cls, *, agent_id, display_name, description, version, input_model, output_model, runner,
               capabilities=(), required_permissions=(), requires_llm=False, supported_modes=("inline",), metadata=None):
        if not isinstance(input_model, type) or not issubclass(input_model, BaseModel):
            raise TypeError("input_model must be a Pydantic model class")
        if not isinstance(output_model, type) or not issubclass(output_model, BaseModel):
            raise TypeError("output_model must be a Pydantic model class")
        if not callable(runner):
            raise TypeError("runner must be callable")
        cap_set = frozenset(AgentCapability(c) for c in capabilities)
        permission_set = frozenset(str(p) for p in required_permissions)
        official_llm_agents = {
            "business_logic_analyst", "code_quality_analyst", "security_analyst",
            "migration_planner", "patch_author", "test_engineer", "review_agent",
        }
        if agent_id == "structure_analyzer" and (requires_llm or "llm.invoke" in permission_set):
            raise ValueError("structure_analyzer cannot require or receive LLM access")
        if agent_id in official_llm_agents and not requires_llm:
            raise ValueError("official reasoning agents must require the llm_gateway")
        if requires_llm and AgentCapability.LLM_REASONING not in cap_set:
            raise ValueError("LLM agents must declare llm_reasoning capability")
        contract = AgentContract(
            agent_id=agent_id, display_name=display_name, description=description, version=version,
            input_schema=input_model.model_json_schema(), output_schema=output_model.model_json_schema(),
            capabilities=cap_set, required_permissions=permission_set,
            requires_llm=requires_llm, supported_modes=frozenset(supported_modes), metadata=metadata or {},
        )
        return cls(contract, input_model, output_model, runner)


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, AgentDefinition] = {}
        self._sealed = False

    def register(self, definition: AgentDefinition) -> None:
        if self._sealed:
            raise RuntimeError("Agent registry is sealed.")
        if not isinstance(definition, AgentDefinition):
            raise TypeError("definition must be an AgentDefinition")
        agent_id = definition.contract.agent_id
        if agent_id in self._agents:
            raise DuplicateAgentError("An agent with this stable ID is already registered.")
        self._agents[agent_id] = definition

    def seal(self):
        self._sealed = True

    def resolve(self, agent_id: str) -> AgentDefinition:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise UnknownAgentError("No agent is registered with this stable ID.", details={"agent_id": agent_id}) from exc

    def metadata(self) -> tuple[AgentContract, ...]:
        return tuple(item.contract for item in self._agents.values())

    def discover(self, definitions):
        """Register an explicitly supplied trusted iterable; never imports modules."""
        for definition in definitions:
            self.register(definition)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

