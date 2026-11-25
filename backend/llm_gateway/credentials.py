"""Credential reference resolution. Secret values stay inside the gateway boundary."""

import os
from dataclasses import dataclass, field
from typing import Mapping, Protocol

from pydantic import SecretStr

from .pool_contracts import CredentialReference


class CredentialResolutionError(Exception):
    """Safe failure that never contains the secret value."""
    code = "llm_credential_unavailable"


@dataclass(frozen=True, repr=False)
class ResolvedCredential:
    credential_id: str
    value: SecretStr = field(repr=False)


class CredentialResolver(Protocol):
    def resolve(self, reference: CredentialReference) -> ResolvedCredential: ...


class SecretManagerResolver(Protocol):
    """Production extension point. No secret-manager implementation is configured here."""
    def resolve(self, reference: CredentialReference) -> ResolvedCredential: ...


class EnvironmentCredentialResolver:
    """Resolve secret references from backend process environment only."""
    def __init__(self, environ: Mapping[str, str] | None = None):
        self._environ = os.environ if environ is None else environ

    def resolve(self, reference: CredentialReference) -> ResolvedCredential:
        if not reference.secret_ref.startswith("CODEWISE_LLM_CREDENTIAL_"):
            raise CredentialResolutionError("Credential reference is not an environment reference.")
        secret = self._environ.get(reference.secret_ref)
        if not isinstance(secret, str) or not secret.strip() or len(secret) > 16_384 or "\x00" in secret:
            raise CredentialResolutionError("Configured LLM credential is missing or malformed.")
        return ResolvedCredential(credential_id=reference.credential_id, value=SecretStr(secret))

    def validate(self, references):
        """Validate configured refs at startup; resolved values are immediately discarded."""
        for reference in references:
            self.resolve(reference)

