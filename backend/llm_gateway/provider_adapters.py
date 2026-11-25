"""Groq and Gemini SDK adapters behind the secret-isolated gateway contract."""

import json
from collections.abc import Mapping
from datetime import timezone
from email.utils import parsedate_to_datetime

from .contracts import ModelRoute, ProviderResponse, Usage
from .credentials import ResolvedCredential
from .errors import ProviderCallError, ProviderFailureKind


def _get(value, key, default=None):
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _enum_text(value):
    if value is None:
        return None
    name = getattr(value, "name", None)
    return str(name if name is not None else value).split(".")[-1]


def _retry_after(exc):
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            moment = parsedate_to_datetime(str(value))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            from datetime import datetime
            return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _normalize_error(exc, *, structured_output=False):
    """Map SDK/transport failures to the gateway's safe retry contract."""
    if isinstance(exc, ProviderCallError):
        return exc
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    try:
        status = int(getattr(status, "value", status))
    except (TypeError, ValueError):
        status = None
    symbolic_status = str(getattr(exc, "status", "")).upper()
    name = type(exc).__name__.casefold()
    retry_after = _retry_after(exc)

    if status == 429 and any(term in symbolic_status for term in ("QUOTA", "RESOURCE_EXHAUSTED")):
        kind = ProviderFailureKind.QUOTA_EXHAUSTED
    elif status == 429:
        kind = ProviderFailureKind.RATE_LIMIT
    elif status in {408, 425}:
        kind = ProviderFailureKind.NETWORK_RETRYABLE
    elif status in {401, 403}:
        kind = ProviderFailureKind.AUTHENTICATION
    elif status == 404:
        kind = ProviderFailureKind.UNSUPPORTED_MODEL
    elif status == 400:
        kind = (ProviderFailureKind.UNSUPPORTED_CAPABILITY if structured_output
                else ProviderFailureKind.INVALID_REQUEST)
    elif status is not None and status >= 500:
        kind = ProviderFailureKind.SERVER_TRANSIENT
    elif any(term in name for term in ("timeout", "connection", "transport", "network")):
        kind = ProviderFailureKind.NETWORK_RETRYABLE
    else:
        kind = ProviderFailureKind.PERMANENT
    return ProviderCallError(kind, retry_after_seconds=retry_after)


class _CapabilityCheckedAdapter:
    provider_id = ""

    def __init__(self, *, model_capabilities: Mapping[str, object], client_factory=None):
        # This is deployment-supplied, operator-verified metadata, not a baked-in
        # model catalog. Unknown models and unverified features fail closed.
        self._model_capabilities = {
            model: frozenset(getattr(capabilities, "features", capabilities))
            for model, capabilities in model_capabilities.items()
        }
        self._client_factory = client_factory

    def validate_route(self, route: ModelRoute):
        if route.provider_id != self.provider_id:
            raise ValueError("Route provider does not match this adapter.")
        features = self._model_capabilities.get(route.model_id)
        if features is None:
            raise ValueError("Model is absent from the verified capability manifest.")
        declared = set(route.capabilities)
        if route.supports_structured_output:
            declared.add("structured_output")
        if not declared.issubset(features):
            raise ValueError("Route declares model capabilities absent from its verified manifest.")
        return features

    def _validate_request(self, route, request):
        try:
            features = self.validate_route(route)
        except ValueError:
            raise ProviderCallError(ProviderFailureKind.UNSUPPORTED_CAPABILITY) from None
        if request.required_capabilities - features:
            raise ProviderCallError(ProviderFailureKind.UNSUPPORTED_CAPABILITY)
        if "max_output_tokens" not in features:
            raise ProviderCallError(ProviderFailureKind.UNSUPPORTED_CAPABILITY)
        if request.output_schema and (not route.supports_structured_output or
                                      "structured_output" not in features):
            raise ProviderCallError(ProviderFailureKind.UNSUPPORTED_CAPABILITY)
        for field, capability in (("temperature", "temperature"), ("top_p", "top_p"),
                                  ("seed", "seed")):
            if getattr(request, field) is not None and capability not in features:
                raise ProviderCallError(ProviderFailureKind.UNSUPPORTED_CAPABILITY)
        if request.stop and "stop_sequences" not in features:
            raise ProviderCallError(ProviderFailureKind.UNSUPPORTED_CAPABILITY)
        if any(message.role == "system" for message in request.messages) and "system_messages" not in features:
            raise ProviderCallError(ProviderFailureKind.UNSUPPORTED_CAPABILITY)
        if any(message.role == "tool" for message in request.messages):
            # GatewayMessage has no tool-call identity, so mapping it to either
            # provider's native tool protocol would be lossy.
            raise ProviderCallError(ProviderFailureKind.UNSUPPORTED_CAPABILITY)

    def _make_client(self, credential, timeout_seconds):
        if not isinstance(credential, ResolvedCredential):
            raise ProviderCallError(ProviderFailureKind.AUTHENTICATION)
        secret = credential.value.get_secret_value()
        if not secret:
            raise ProviderCallError(ProviderFailureKind.AUTHENTICATION)
        return self._client_factory(api_key=secret, timeout_seconds=timeout_seconds)

    @staticmethod
    def _close(client):
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Client cleanup must not replace the provider result or safe error.
                pass


class GroqProviderAdapter(_CapabilityCheckedAdapter):
    """Synchronous Groq chat-completions adapter; SDK retries are disabled."""
    provider_id = "groq"

    def __init__(self, *, model_capabilities: Mapping[str, object], client_factory=None):
        super().__init__(model_capabilities=model_capabilities,
                         client_factory=client_factory or self._default_client)

    @staticmethod
    def _default_client(*, api_key, timeout_seconds):
        from groq import Groq
        return Groq(api_key=api_key, timeout=timeout_seconds, max_retries=0)

    def complete(self, route, request, *, timeout_seconds, credential=None):
        self._validate_request(route, request)
        if timeout_seconds <= 0:
            raise ProviderCallError(ProviderFailureKind.NETWORK_RETRYABLE)
        try:
            client = self._make_client(credential, timeout_seconds)
        except ProviderCallError:
            raise
        except Exception as exc:
            raise _normalize_error(exc, structured_output=bool(request.output_schema)) from None
        try:
            messages = [{"role": message.role, "content": message.content} for message in request.messages]
            params = {"model": route.model_id, "messages": messages,
                      "max_completion_tokens": request.max_output_tokens}
            for field in ("temperature", "top_p", "seed"):
                value = getattr(request, field)
                if value is not None:
                    params[field] = value
            if request.stop:
                params["stop"] = list(request.stop)
            if request.output_schema:
                params["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "codewise_agent_output", "strict": False,
                    "schema": request.output_schema}}
            try:
                response = client.chat.completions.create(**params)
            except Exception as exc:
                raise _normalize_error(exc, structured_output=bool(request.output_schema)) from None
            choices = _get(response, "choices") or ()
            if not choices:
                raise ProviderCallError(ProviderFailureKind.PERMANENT)
            choice = choices[0]
            message = _get(choice, "message")
            refusal = _get(message, "refusal")
            content = _get(message, "content")
            if refusal and not content:
                raise ProviderCallError(ProviderFailureKind.SAFETY_REFUSAL)
            if not isinstance(content, str) or not content.strip():
                raise ProviderCallError(ProviderFailureKind.STRUCTURED_OUTPUT_INVALID
                                        if request.output_schema else ProviderFailureKind.PERMANENT)
            if request.output_schema:
                try:
                    content = json.loads(content)
                except (TypeError, json.JSONDecodeError):
                    raise ProviderCallError(ProviderFailureKind.STRUCTURED_OUTPUT_INVALID) from None
                if not isinstance(content, dict):
                    raise ProviderCallError(ProviderFailureKind.STRUCTURED_OUTPUT_INVALID)
            raw_usage = _get(response, "usage")
            usage = Usage(input_tokens=_get(raw_usage, "prompt_tokens"),
                          output_tokens=_get(raw_usage, "completion_tokens"))
            metadata = {}
            finish_reason = _enum_text(_get(choice, "finish_reason"))
            if finish_reason:
                metadata["finish_reason"] = finish_reason
            response_id = _get(response, "id")
            if response_id:
                metadata["request_id"] = str(response_id)
            return ProviderResponse(content=content, usage=usage, metadata=metadata)
        finally:
            self._close(client)


class GeminiProviderAdapter(_CapabilityCheckedAdapter):
    """Synchronous Gemini generate-content adapter; SDK retries are disabled."""
    provider_id = "gemini"

    def __init__(self, *, model_capabilities: Mapping[str, object], client_factory=None,
                 api_version: str | None = None):
        self.api_version = api_version
        if client_factory is None:
            def client_factory(*, api_key, timeout_seconds):
                return self._default_client(api_key=api_key, timeout_seconds=timeout_seconds,
                                            api_version=self.api_version)
        super().__init__(model_capabilities=model_capabilities, client_factory=client_factory)

    @staticmethod
    def _default_client(*, api_key, timeout_seconds, api_version=None):
        from google import genai
        from google.genai import types
        options = {"timeout": max(1, int(timeout_seconds * 1000)),
                   "retry_options": types.HttpRetryOptions(attempts=1)}
        if api_version is not None:
            options["api_version"] = api_version
        http_options = types.HttpOptions(**options)
        return genai.Client(api_key=api_key, http_options=http_options)

    @staticmethod
    def _contents(request):
        system = [item.content for item in request.messages if item.role == "system"]
        contents = [{"role": "model" if item.role == "assistant" else "user",
                     "parts": [{"text": item.content}]}
                    for item in request.messages if item.role != "system"]
        if not contents:
            raise ProviderCallError(ProviderFailureKind.INVALID_REQUEST)
        return "\n\n".join(system) if system else None, contents

    def complete(self, route, request, *, timeout_seconds, credential=None):
        self._validate_request(route, request)
        if timeout_seconds <= 0:
            raise ProviderCallError(ProviderFailureKind.NETWORK_RETRYABLE)
        try:
            client = self._make_client(credential, timeout_seconds)
        except ProviderCallError:
            raise
        except Exception as exc:
            raise _normalize_error(exc, structured_output=bool(request.output_schema)) from None
        try:
            system_instruction, contents = self._contents(request)
            config = {"max_output_tokens": request.max_output_tokens}
            if system_instruction:
                config["system_instruction"] = system_instruction
            for field in ("temperature", "top_p", "seed"):
                value = getattr(request, field)
                if value is not None:
                    config[field] = value
            if request.stop:
                config["stop_sequences"] = list(request.stop)
            if request.output_schema:
                config["response_mime_type"] = "application/json"
                config["response_json_schema"] = request.output_schema
            try:
                response = client.models.generate_content(model=route.model_id,
                                                         contents=contents, config=config)
            except Exception as exc:
                raise _normalize_error(exc, structured_output=bool(request.output_schema)) from None

            prompt_feedback = _get(response, "prompt_feedback")
            block_reason = str(_enum_text(_get(prompt_feedback, "block_reason")) or "").casefold()
            if any(term in block_reason for term in ("block", "safety", "prohibited")):
                raise ProviderCallError(ProviderFailureKind.SAFETY_REFUSAL)
            candidates = _get(response, "candidates") or ()
            candidate = candidates[0] if candidates else None
            finish_reason = _enum_text(_get(candidate, "finish_reason"))
            if finish_reason and any(word in finish_reason.casefold() for word in ("safety", "block")):
                raise ProviderCallError(ProviderFailureKind.SAFETY_REFUSAL)

            parsed = _get(response, "parsed") if request.output_schema else None
            text = _get(response, "text")
            if request.output_schema:
                if isinstance(parsed, Mapping):
                    content = dict(parsed)
                elif callable(getattr(parsed, "model_dump", None)):
                    content = parsed.model_dump()
                elif isinstance(text, str) and text.strip():
                    try:
                        content = json.loads(text)
                    except (TypeError, json.JSONDecodeError):
                        raise ProviderCallError(ProviderFailureKind.STRUCTURED_OUTPUT_INVALID) from None
                else:
                    raise ProviderCallError(ProviderFailureKind.STRUCTURED_OUTPUT_INVALID)
                if not isinstance(content, dict):
                    raise ProviderCallError(ProviderFailureKind.STRUCTURED_OUTPUT_INVALID)
            elif isinstance(text, str) and text:
                content = text
            else:
                raise ProviderCallError(ProviderFailureKind.PERMANENT)

            raw_usage = _get(response, "usage_metadata")
            usage = Usage(input_tokens=_get(raw_usage, "prompt_token_count"),
                          output_tokens=_get(raw_usage, "candidates_token_count"))
            metadata = {}
            if finish_reason:
                metadata["finish_reason"] = finish_reason
            response_id = _get(response, "response_id")
            if response_id:
                metadata["request_id"] = str(response_id)
            model_version = _get(response, "model_version")
            if model_version:
                metadata["model_version"] = str(model_version)
            return ProviderResponse(content=content, usage=usage, metadata=metadata)
        finally:
            self._close(client)
