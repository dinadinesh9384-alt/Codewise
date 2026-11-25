"""Typed values returned by the GitHub provider boundary."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GitHubRepositoryLocator(ProviderContract):
    owner: str = Field(min_length=1, max_length=39, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.name}"


class GitHubRepository(ProviderContract):
    provider_repository_id: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=39, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    canonical_url: str = Field(pattern=r"^https://github\.com/[^/]+/[^/]+$")
    visibility: Literal["public"]
    default_branch: str | None = Field(default=None, max_length=255)

    @property
    def locator(self) -> GitHubRepositoryLocator:
        return GitHubRepositoryLocator(owner=self.owner, name=self.name)


class ResolvedCommit(ProviderContract):
    repository: GitHubRepository
    requested_ref: str = Field(min_length=1, max_length=255)
    ref_kind: Literal["branch", "tag", "commit"]
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
