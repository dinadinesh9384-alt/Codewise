class GatewayError(Exception):
    """Safe gateway failure; messages never include provider exception text."""
    code = "gateway_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class PolicyDenied(GatewayError): code = "llm_policy_denied"
class UnknownModel(GatewayError): code = "llm_unknown_model"
class BudgetExceeded(GatewayError): code = "llm_budget_exceeded"
class RateLimited(GatewayError): code = "llm_rate_limited"
class ProviderUnavailable(GatewayError): code = "llm_provider_unavailable"
class ProviderFailure(GatewayError): code = "llm_provider_failure"
class InvalidProviderResponse(GatewayError): code = "llm_invalid_response"
class GatewayTimeout(GatewayError): code = "llm_timeout"
class InvalidGatewayRequest(GatewayError): code = "llm_invalid_request"


from enum import StrEnum
import math


class ProviderFailureKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TEMPORARY_OUTAGE = "temporary_outage"
    NETWORK_RETRYABLE = "network_retryable"
    SERVER_TRANSIENT = "server_transient"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_MODEL = "unsupported_model"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    STRUCTURED_OUTPUT_INVALID = "structured_output_invalid"
    SAFETY_REFUSAL = "safety_refusal"
    PERMANENT = "permanent"


class ProviderCallError(Exception):
    """Provider adapter's normalized, non-secret failure contract."""
    def __init__(self, kind: ProviderFailureKind, *, retry_after_seconds: float | None = None):
        super().__init__(kind.value)
        self.kind = ProviderFailureKind(kind)
        if retry_after_seconds is not None and (not math.isfinite(retry_after_seconds) or retry_after_seconds < 0):
            raise ValueError("retry_after_seconds must not be negative")
        self.retry_after_seconds = retry_after_seconds


class PoolConfigurationError(GatewayError): code = "llm_pool_configuration_error"
class PoolDisabled(GatewayError): code = "llm_pool_disabled"
class UnknownAgentPool(GatewayError): code = "llm_unknown_agent"
class CrossPoolAccessDenied(GatewayError): code = "llm_cross_pool_access_denied"
class PoolBudgetExceeded(BudgetExceeded): code = "llm_pool_budget_exceeded"
class PoolExhausted(ProviderUnavailable): code = "llm_pool_exhausted"
class ProviderRejected(ProviderFailure): code = "llm_provider_rejected"
