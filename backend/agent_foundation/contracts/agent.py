from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentCapability(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM_REASONING = "llm_reasoning"
    REPOSITORY_READ = "repository_read"
    TOOLS = "tools"


class ExecutionMode(StrEnum):
    INLINE = "inline"
    GRAPH = "graph"


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    contract_version: str = Field(default="agent.definition.v1", pattern=r"^agent\.definition\.v[1-9][0-9]*$")
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    version: str = Field(min_length=1, max_length=64)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: frozenset[AgentCapability] = frozenset()
    required_permissions: frozenset[str] = frozenset()
    requires_llm: bool = False
    supported_modes: frozenset[ExecutionMode] = frozenset({ExecutionMode.INLINE})
    metadata: dict[str, Any] = Field(default_factory=dict)

