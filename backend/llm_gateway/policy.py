from pydantic import BaseModel, ConfigDict, Field

from .contracts import ModelTier


class RequestLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_input_bytes: int = Field(default=64_000, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    max_requests_per_minute: int = Field(default=30, ge=1)
    max_concurrent_requests: int = Field(default=4, ge=1)
    max_requests_per_execution: int = Field(default=10, ge=1)
    max_requests_per_agent: int = Field(default=100, ge=1)
    max_cost_per_request: float | None = Field(default=None, ge=0)


class GatewayPolicy(BaseModel):
    """An empty policy is intentionally deny-all."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    allowed_agents: frozenset[str] = frozenset()
    allowed_models: frozenset[tuple[str, str]] = frozenset()
    enabled_tiers: frozenset[ModelTier] = frozenset()
    allow_paid: bool = False
    max_retries: int = Field(default=0, ge=0, le=3)
    circuit_breaker_failures: int = Field(default=3, ge=1)
    circuit_breaker_cooldown_seconds: float = Field(default=30, gt=0)
    limits: RequestLimits = Field(default_factory=RequestLimits)
