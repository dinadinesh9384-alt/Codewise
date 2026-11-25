"""Safe, structured errors emitted at agent execution boundaries."""

from pydantic import BaseModel, ConfigDict, Field


class AgentFoundationError(Exception):
    code = "agent_foundation_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def as_contract(self):
        return ErrorInfo(code=self.code, message=self.message, details=self.details)


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(max_length=500)
    details: dict = Field(default_factory=dict)


def _error(name, code):
    return type(name, (AgentFoundationError,), {"code": code})


UnknownAgentError = _error("UnknownAgentError", "unknown_agent")
DuplicateAgentError = _error("DuplicateAgentError", "duplicate_agent")
InvalidInputError = _error("InvalidInputError", "invalid_input")
InvalidOutputError = _error("InvalidOutputError", "invalid_output")
PermissionDeniedError = _error("PermissionDeniedError", "permission_denied")
SnapshotUnavailableError = _error("SnapshotUnavailableError", "snapshot_unavailable")
EvidenceValidationError = _error("EvidenceValidationError", "evidence_invalid")
ToolExecutionError = _error("ToolExecutionError", "tool_execution_failed")
ToolTimeoutError = _error("ToolTimeoutError", "tool_timeout")
AgentTimeoutError = _error("AgentTimeoutError", "agent_timeout")
InvalidExecutionTransitionError = _error("InvalidExecutionTransitionError", "invalid_execution_transition")
LLMGatewayUnavailableError = _error("LLMGatewayUnavailableError", "llm_gateway_unavailable")
UnsupportedCapabilityError = _error("UnsupportedCapabilityError", "unsupported_capability")
ExecutionCancelledError = _error("ExecutionCancelledError", "execution_cancelled")
