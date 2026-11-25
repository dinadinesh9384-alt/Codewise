from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .evidence import EvidenceReference


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    EVIDENCE_SUPPORTED = "evidence_supported"
    DETERMINISTICALLY_VERIFIED = "deterministically_verified"
    REJECTED = "rejected"


class FindingCategory(StrEnum):
    STRUCTURE = "structure"
    BUSINESS_LOGIC = "business_logic"
    QUALITY = "quality"
    SECURITY = "security"
    MIGRATION = "migration"
    PATCH = "patch"
    TEST = "test"
    REVIEW = "review"
    OTHER = "other"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    finding_id: str = Field(min_length=1, max_length=128)
    category: FindingCategory
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=10000)
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[EvidenceReference, ...] = ()
    related_symbols: tuple[str, ...] = ()
    related_files: tuple[str, ...] = ()
    suggested_next_action: str | None = Field(default=None, max_length=2000)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    uncertainty: str | None = Field(default=None, max_length=2000)


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    summary: str = Field(default="", max_length=10000)
    findings: tuple[Finding, ...] = ()
    warnings: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
