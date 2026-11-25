from typing import Annotated, TypedDict


def merge_by_key(left: dict | None, right: dict | None) -> dict:
    result = dict(left or {})
    for key, value in (right or {}).items():
        if key in result and result[key] != value:
            raise ValueError("Conflicting writes to orchestration state key.")
        result[key] = value
    return result


def append_events(left: list | None, right: list | None) -> list:
    return list(left or ()) + list(right or ())


class WorkflowState(TypedDict, total=False):
    execution_id: str
    workspace_id: str
    repository_id: str
    snapshot_id: str
    commit_sha: str
    manifest_digest: str
    correlation_id: str
    trace_id: str
    deadline: str | None
    approval_required: bool
    selected_groups: list[str]
    selected_agents: list[str]
    selected: dict[str, bool]
    statuses: Annotated[dict[str, str], merge_by_key]
    agent_results: Annotated[dict[str, dict], merge_by_key]
    errors: Annotated[dict[str, str], merge_by_key]
    events: Annotated[list[dict], append_events]
    approval: str | None
    workflow_status: str
