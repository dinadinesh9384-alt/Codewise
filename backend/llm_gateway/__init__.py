"""Provider-independent, fail-closed LLM invocation boundary."""

from .contracts import GatewayRequest, GatewayResponse, ModelRoute, ProviderResponse, Usage
from .gateway import LLMGateway
from .policy import GatewayPolicy, RequestLimits
from .provider_adapters import GeminiProviderAdapter, GroqProviderAdapter
from .provider_registry import ProviderAdapterRegistry
from .pool_contracts import (AGENT_POOL_MAP, CredentialReference, PoolConfiguration,
                             PoolConfigurations, PoolId, PoolLimits, PoolRetryPolicy,
                             PoolRoute, QuotaIdentity, QuotaLimits)
from .credentials import EnvironmentCredentialResolver, ResolvedCredential, SecretManagerResolver

__all__ = ["GatewayRequest", "GatewayResponse", "ModelRoute", "ProviderResponse", "Usage",
           "LLMGateway", "GatewayPolicy", "RequestLimits", "AGENT_POOL_MAP", "CredentialReference",
           "PoolConfiguration", "PoolConfigurations", "PoolId", "PoolLimits", "PoolRetryPolicy",
           "PoolRoute", "QuotaIdentity", "QuotaLimits", "EnvironmentCredentialResolver",
           "ResolvedCredential", "SecretManagerResolver", "GroqProviderAdapter",
           "GeminiProviderAdapter", "ProviderAdapterRegistry"]
