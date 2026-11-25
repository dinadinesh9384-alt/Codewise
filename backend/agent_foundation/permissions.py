"""Least-privilege capability vocabulary and execution-time checks."""

from enum import StrEnum

from .errors import PermissionDeniedError


class Permission(StrEnum):
    REPOSITORY_READ = "repository.read"
    REPOSITORY_SEARCH = "repository.search"
    ANALYSIS_RUN = "analysis.run"
    EVIDENCE_READ = "evidence.read"
    LLM_INVOKE = "llm.invoke"
    PATCH_PROPOSE = "patch.propose"
    PATCH_APPLY = "patch.apply"
    TESTS_EXECUTE = "tests.execute"
    SANDBOX_EXECUTE = "sandbox.execute"
    GITHUB_PUBLISH = "github.publish"


def require_permissions(granted, required):
    missing = frozenset(required) - frozenset(granted)
    if missing:
        raise PermissionDeniedError("Execution is not authorized for this operation.", details={"missing": sorted(str(x) for x in missing)})
