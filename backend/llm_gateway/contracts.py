from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelTier(StrEnum):
    LOCAL = "local"
    FREE = "free"
    PAID = "paid"


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: str = Field(pattern=r"^(system|user|assistant|tool)$")
    content: str = Field(max_length=200_000)


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    pool_id: str | None = Field(default=None, pattern=r"^pool_[abc]$")
    execution_id: str = Field(min_length=1, max_length=128)
    provider_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    messages: tuple[LLMMessage, ...] = Field(min_length=1, max_length=100)
    stop: tuple[str, ...] = Field(default=(), max_length=16)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    seed: int | None = None
    output_schema: dict[str, Any] | None = None
    task: str = Field(default="analysis", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    required_capabilities: frozenset[str] = frozenset()
    correlation_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    deadline: datetime | None = None
    max_output_tokens: int = Field(default=1024, ge=1, le=32768)
    max_input_bytes: int | None = Field(default=None, ge=1)
    max_cost: float | None = Field(default=None, ge=0)
    idempotent: bool = False

    @model_validator(mode="after")
    def schema_is_object(self):
        if self.output_schema is not None and not isinstance(self.output_schema, dict):
            raise ValueError("output_schema must be a JSON object")
        return self

    @model_validator(mode="after")
    def route_is_complete(self):
        if (self.provider_id is None) != (self.model_id is None):
            raise ValueError("provider_id and model_id must be supplied together")
        if any(not stop or len(stop) > 256 for stop in self.stop):
            raise ValueError("stop sequences must contain 1 to 256 characters")
        return self


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    availability: str = Field(default="unavailable", pattern=r"^(available|unavailable|partial)$")


class ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content: str | dict[str, Any]
    usage: Usage = Field(default_factory=Usage)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class GatewayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    output: str | dict[str, Any]
    provider_id: str
    model_id: str
    pool_id: str | None = None
    usage: Usage
    retries: int = Field(ge=0)


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    model_id: str = Field(min_length=1, max_length=128)
    tier: ModelTier
    supported_tasks: frozenset[str] = frozenset({"analysis"})
    capabilities: frozenset[str] = frozenset()
    supports_structured_output: bool = False
    cost_per_1k_input_tokens: float | None = Field(default=None, ge=0)
    cost_per_1k_output_tokens: float | None = Field(default=None, ge=0)

    @property
    def price_known(self):
        return self.cost_per_1k_input_tokens is not None and self.cost_per_1k_output_tokens is not None
