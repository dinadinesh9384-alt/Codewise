from typing import Protocol

from .contracts import ModelRoute, ProviderResponse
from .credentials import ResolvedCredential


class ProviderAdapter(Protocol):
    """Only this gateway-facing interface may be implemented by provider adapters."""
    def complete(self, route: ModelRoute, request, *, timeout_seconds: float,
                 credential: ResolvedCredential | None = None) -> ProviderResponse: ...
