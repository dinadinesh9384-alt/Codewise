"""Registry of gateway transport adapters, with no provider SDK bypass path."""

from collections.abc import Iterator, Mapping
from types import MappingProxyType

from .provider_adapters import GeminiProviderAdapter, GroqProviderAdapter


class ProviderAdapterRegistry(Mapping):
    """Immutable provider-id to adapter mapping accepted by ``LLMGateway``."""

    def __init__(self, adapters: Mapping[str, object]):
        checked = {}
        for provider_id, adapter in adapters.items():
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError("Provider registry keys must be non-empty provider IDs.")
            if not callable(getattr(adapter, "complete", None)):
                raise TypeError("Registered provider adapters must implement complete().")
            checked[provider_id] = adapter
        self._adapters = MappingProxyType(checked)

    def __getitem__(self, provider_id):
        return self._adapters[provider_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._adapters)

    def __len__(self):
        return len(self._adapters)

    @classmethod
    def groq_and_gemini(cls, *, model_capabilities, groq_client_factory=None,
                        gemini_client_factory=None, gemini_api_version=None):
        """Build both adapters from verified, operator-supplied model metadata.

        ``model_capabilities`` maps provider IDs to model-id/feature mappings. No
        model IDs or capabilities are bundled with the application.
        """
        return cls({
            "groq": GroqProviderAdapter(
                model_capabilities=model_capabilities.get("groq", {}),
                client_factory=groq_client_factory,
            ),
            "gemini": GeminiProviderAdapter(
                model_capabilities=model_capabilities.get("gemini", {}),
                client_factory=gemini_client_factory,
                api_version=gemini_api_version,
            ),
        })
