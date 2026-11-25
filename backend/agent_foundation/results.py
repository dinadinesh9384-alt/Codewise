"""Versioned execution outcomes; no persistence is performed here."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .contracts.execution import ExecutionStatus, ModelUsage, TimingMetadata, ValidationStatus
from .contracts.evidence import EvidenceReference
from .contracts.findings import Finding
from .errors import ErrorInfo


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "execution.result.v1"
    execution_id: UUID
    agent_id: str
    agent_version: str
    status: ExecutionStatus
    output: Any = None
    findings: tuple[Finding, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    tool_invocations: tuple[dict, ...] = ()
    validation_status: ValidationStatus = ValidationStatus.NOT_RUN
    errors: tuple[ErrorInfo, ...] = ()
    timing: TimingMetadata = Field(default_factory=TimingMetadata)
    model_usage: ModelUsage | None = None
    correlation_id: str
    trace_id: str
    warnings: tuple[str, ...] = ()
