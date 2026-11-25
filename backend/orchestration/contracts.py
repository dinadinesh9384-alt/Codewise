from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


GROUPS = ("code_quality_security_planning", "patch_engineering", "final_review")
OFFICIAL = ("structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst",
            "migration_planner", "patch_author", "test_engineer", "review_agent")


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    correlation_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    groups: frozenset[str] = Field(default_factory=lambda: frozenset(GROUPS))
    agents: frozenset[str] = Field(default_factory=lambda: frozenset(OFFICIAL))
    deadline: datetime | None = None
    approval_required: bool = False

    @field_validator("groups")
    @classmethod
    def known_groups(cls, value):
        if value - set(GROUPS): raise ValueError("Unknown orchestration group.")
        return value

    @field_validator("agents")
    @classmethod
    def known_agents(cls, value):
        if value - set(OFFICIAL): raise ValueError("Unknown official agent ID.")
        return value

    def selected(self, agent_id: str) -> bool:
        if agent_id in {"structure_analyzer", "business_logic_analyst"}:
            return True
        if agent_id not in self.agents:
            return False
        group = {"code_quality_analyst": GROUPS[0], "security_analyst": GROUPS[0], "migration_planner": GROUPS[0],
                 "patch_author": GROUPS[1], "test_engineer": GROUPS[1], "review_agent": GROUPS[2]}[agent_id]
        return group in self.groups


class AgentWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    status: Literal["completed", "failed", "cancelled", "timed_out", "skipped", "unavailable"]
    output: dict | None = None
    error_codes: tuple[str, ...] = ()


class WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    status: Literal["completed", "partial", "failed", "cancelled", "timed_out"]
    agents: tuple[AgentWorkflowResult, ...]
    errors: tuple[str, ...] = ()
    events: tuple[dict, ...] = ()
    approval: str | None = None
    final_review_status: Literal[
        "ready_for_human_review", "ready_with_warnings", "changes_required", "blocked", "incomplete",
        "not_performed", "unavailable", "failed", "cancelled", "timed_out",
    ] = "not_performed"
