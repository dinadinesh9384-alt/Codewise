"""Strict, secret-free configuration for isolated agent credential pools."""

from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ModelRoute


class PoolId(StrEnum):
    POOL_A = "pool_a"
    POOL_B = "pool_b"
    POOL_C = "pool_c"


AGENT_POOL_MAP = MappingProxyType({
    "business_logic_analyst": PoolId.POOL_A,
    "code_quality_analyst": PoolId.POOL_A,
    "security_analyst": PoolId.POOL_B,
    "migration_planner": PoolId.POOL_B,
    "patch_author": PoolId.POOL_B,
    "test_engineer": PoolId.POOL_C,
    "review_agent": PoolId.POOL_C,
})


class QuotaIdentity(BaseModel):
    """Operator-declared shared quota bucket, independent of an API key."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=128)
    organization_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    quota_bucket_id: str = Field(min_length=1, max_length=128)

    @property
    def key(self):
        # Operator-assigned bucket IDs may span accounts/projects when those identities share real quota.
        return (self.provider_id, self.quota_bucket_id)


class CredentialReference(BaseModel):
    """A non-secret identifier and environment/secret-manager reference."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    credential_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    secret_ref: str = Field(pattern=r"^(?:CODEWISE_LLM_CREDENTIAL_[A-Z0-9_]{1,80}|secret-ref:[A-Za-z0-9_./:-]{1,180})$")
    identity: QuotaIdentity


class PoolLimits(BaseModel):
    """Local admission limits, not claims about a provider's actual quota."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_requests_per_minute: int = Field(gt=0)
    max_requests_per_execution: int = Field(gt=0)
    max_requests_per_agent_per_hour: int = Field(gt=0)
    max_input_bytes_per_request: int = Field(gt=0)
    max_output_tokens_per_request: int = Field(gt=0)
    max_input_tokens_per_hour: int = Field(gt=0)
    max_output_tokens_per_hour: int = Field(gt=0)
    max_concurrent_requests: int = Field(gt=0)
    max_cost_per_request: float | None = Field(default=None, ge=0)


class QuotaLimits(BaseModel):
    """Local shared accounting limits for a declared account/project bucket."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_requests_per_minute: int = Field(gt=0)
    max_input_tokens_per_hour: int = Field(gt=0)
    max_output_tokens_per_hour: int = Field(gt=0)
    max_concurrent_requests: int = Field(gt=0)


class PoolRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_retries_per_credential: int = Field(default=1, ge=0, le=3)
    initial_backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    max_backoff_seconds: float = Field(default=30, gt=0, le=120)
    max_retry_after_seconds: float = Field(default=60, gt=0, le=300)
    jitter_fraction: float = Field(default=0.2, ge=0, le=1)
    circuit_breaker_failures: int = Field(default=3, ge=1)
    circuit_breaker_cooldown_seconds: float = Field(default=30, gt=0, le=300)


class PoolRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    route: ModelRoute
    credentials: tuple[CredentialReference, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def route_credential_providers_match(self):
        if any(item.identity.provider_id != self.route.provider_id for item in self.credentials):
            raise ValueError("Credential provider identity must match its route.")
        if len({item.credential_id for item in self.credentials}) != len(self.credentials):
            raise ValueError("Credential IDs must be unique within a route.")
        if len({item.secret_ref for item in self.credentials}) != len(self.credentials):
            raise ValueError("Secret references must be unique within a route.")
        return self

    @property
    def primary(self):
        return self.credentials[0]

    @property
    def fallbacks(self):
        return self.credentials[1:]


class PoolConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pool_id: PoolId
    enabled: bool = False
    routes: tuple[PoolRoute, ...] = Field(default=(), max_length=8)
    limits: PoolLimits | None = None
    quota_limits: QuotaLimits | None = None
    retry: PoolRetryPolicy = Field(default_factory=PoolRetryPolicy)
    paid_authorized: bool = False

    @model_validator(mode="after")
    def enabled_pool_is_complete(self):
        if self.enabled and (not self.routes or self.limits is None or self.quota_limits is None):
            raise ValueError("Enabled pools require routes, local limits, and quota-accounting limits.")
        if any(route.route.tier.value == "paid" for route in self.routes) and not self.paid_authorized:
            # Routes may be staged while disabled, but activation requires explicit authorization.
            if self.enabled:
                raise ValueError("Paid routes require explicit pool authorization.")
        return self


class PoolConfigurations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pools: dict[PoolId, PoolConfiguration] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_three_independent_pools(self):
        if set(self.pools) != set(PoolId):
            raise ValueError("Configuration must define Pool A, Pool B, and Pool C.")
        if any(key != value.pool_id for key, value in self.pools.items()):
            raise ValueError("Pool configuration key and pool_id must match.")
        refs_by_pool: dict[PoolId, set[str]] = {}
        ids_by_pool: dict[PoolId, set[str]] = {}
        quota_by_account: dict[tuple, str] = {}
        for pool_id, pool in self.pools.items():
            refs_by_pool[pool_id] = {c.secret_ref for r in pool.routes for c in r.credentials}
            ids_by_pool[pool_id] = {c.credential_id for r in pool.routes for c in r.credentials}
            for route in pool.routes:
                for credential in route.credentials:
                    identity = credential.identity
                    account_scope = (identity.provider_id, identity.account_id, identity.organization_id, identity.project_id)
                    known_bucket = quota_by_account.setdefault(account_scope, identity.quota_bucket_id)
                    if known_bucket != identity.quota_bucket_id:
                        raise ValueError("Credentials in the same provider/account/project must share a quota bucket identity.")
        ids = list(ids_by_pool)
        for index, left in enumerate(ids):
            for right in ids[index + 1:]:
                if refs_by_pool[left] & refs_by_pool[right] or ids_by_pool[left] & ids_by_pool[right]:
                    raise ValueError("Credentials cannot be shared across pools.")
        return self

    @classmethod
    def disabled(cls):
        return cls(pools={pool_id: PoolConfiguration(pool_id=pool_id) for pool_id in PoolId})

    @classmethod
    def from_environment(cls, environ):
        import json
        pools = {}
        for pool_id in PoolId:
            env_name = f"CODEWISE_LLM_{pool_id.value.upper()}_CONFIG"
            raw = environ.get(env_name)
            if raw is None or not raw.strip():
                pools[pool_id] = PoolConfiguration(pool_id=pool_id)
                continue
            try:
                parsed: Any = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError
                if parsed.get("pool_id", pool_id.value) != pool_id.value:
                    raise ValueError
                config = PoolConfiguration.model_validate({**parsed, "pool_id": pool_id})
            except Exception:
                raise ValueError(f"Invalid LLM pool configuration in {env_name}.") from None
            pools[pool_id] = config
        return cls(pools=pools)
