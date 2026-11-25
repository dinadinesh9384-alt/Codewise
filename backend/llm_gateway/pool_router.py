"""Three-pool credential routing with quota identity and fallback isolation."""

import random
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from jsonschema import Draft202012Validator

from agent_foundation.permissions import Permission, require_permissions

from .contracts import GatewayRequest, GatewayResponse, ModelTier, ProviderResponse
from .credentials import CredentialResolver
from .errors import (CrossPoolAccessDenied, GatewayTimeout, InvalidGatewayRequest,
                     InvalidProviderResponse, PoolBudgetExceeded, PoolConfigurationError,
                     PoolDisabled, PoolExhausted, PolicyDenied, ProviderCallError, RateLimited,
                     ProviderFailureKind, ProviderRejected, UnknownAgentPool)
from .pool_contracts import AGENT_POOL_MAP, PoolConfiguration, PoolConfigurations, PoolId, QuotaIdentity, QuotaLimits

_ELIGIBLE = {
    ProviderFailureKind.RATE_LIMIT,
    ProviderFailureKind.QUOTA_EXHAUSTED,
    ProviderFailureKind.TEMPORARY_OUTAGE,
    ProviderFailureKind.NETWORK_RETRYABLE,
    ProviderFailureKind.SERVER_TRANSIENT,
}
_SECRET_METADATA_KEYS = ("token", "secret", "password", "credential", "authorization", "api_key")


@dataclass
class _TokenEntry:
    at: float
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class _Reservation:
    pool_id: PoolId
    quota_key: tuple
    pool_tokens: _TokenEntry
    quota_tokens: _TokenEntry


def _quota_limits_min(left: QuotaLimits, right: QuotaLimits) -> QuotaLimits:
    return QuotaLimits(**{name: min(getattr(left, name), getattr(right, name))
                          for name in QuotaLimits.model_fields})


class PoolCredentialRouter:
    """Only called by LLMGateway; credential values are resolved for one provider call."""
    def __init__(self, *, configurations: PoolConfigurations, credential_resolver: CredentialResolver,
                 providers, global_policy, global_semaphore, observer=None, sleeper=time.sleep, random_value=random.random,
                 monotonic=time.monotonic):
        if not isinstance(configurations, PoolConfigurations):
            raise TypeError("configurations must be PoolConfigurations")
        any_enabled = any(pool.enabled for pool in configurations.pools.values())
        if credential_resolver is None and any_enabled:
            raise PoolConfigurationError("Enabled credential pools require a credential resolver.")
        self.configurations = configurations
        self._credential_resolver = credential_resolver
        self.providers = providers
        self.global_policy = global_policy
        self.global_semaphore = global_semaphore
        self.observer = observer
        self._sleep = sleeper
        self._random = random_value
        self._now = monotonic
        self._lock = threading.RLock()
        self._pool_requests = defaultdict(deque)
        self._execution_requests = defaultdict(deque)
        self._agent_requests = defaultdict(deque)
        self._pool_tokens = defaultdict(deque)
        self._quota_requests = defaultdict(deque)
        self._quota_tokens = defaultdict(deque)
        self._active_pool = defaultdict(int)
        self._active_quota = defaultdict(int)
        self._quota_limits: dict[tuple, QuotaLimits] = {}
        self._circuit_failures = defaultdict(int)
        self._circuit_until = defaultdict(float)
        self._validate_enabled_credentials()
        self._build_quota_limits()

    def _all_references(self):
        return tuple(credential for pool in self.configurations.pools.values() if pool.enabled
                     for route in pool.routes for credential in route.credentials)

    def _validate_enabled_credentials(self):
        for pool in self.configurations.pools.values():
            if not pool.enabled:
                continue
            for route in pool.routes:
                provider = self.providers.get(route.route.provider_id)
                if provider is None:
                    raise PoolConfigurationError("An enabled route has no registered provider adapter.")
                validate_route = getattr(provider, "validate_route", None)
                if callable(validate_route):
                    try:
                        validate_route(route.route)
                    except Exception:
                        raise PoolConfigurationError(
                            "An enabled route failed provider model-capability validation."
                        ) from None
                for reference in route.credentials:
                    try:
                        if self._credential_resolver is None:
                            raise ValueError
                        self._credential_resolver.resolve(reference)
                    except Exception:
                        raise PoolConfigurationError("An enabled pool references a missing or invalid credential.") from None

    def _build_quota_limits(self):
        for pool in self.configurations.pools.values():
            if not pool.enabled:
                continue
            for route in pool.routes:
                for credential in route.credentials:
                    key = credential.identity.key
                    current = self._quota_limits.get(key)
                    self._quota_limits[key] = pool.quota_limits if current is None else _quota_limits_min(current, pool.quota_limits)

    def invoke(self, request, *, permissions):
        if not isinstance(request, GatewayRequest):
            raise InvalidGatewayRequest("LLM request did not match the gateway contract.")
        require_permissions(permissions, {Permission.LLM_INVOKE})
        if not self.global_policy.enabled:
            raise PolicyDenied("LLM gateway is disabled.")
        if request.agent_id == "structure_analyzer":
            raise PolicyDenied("structure_analyzer is never allowed to invoke the LLM gateway.")
        try:
            pool_id = AGENT_POOL_MAP[request.agent_id]
        except KeyError:
            raise UnknownAgentPool("Agent has no authorized LLM pool.") from None
        if request.pool_id is not None and request.pool_id != pool_id.value:
            raise CrossPoolAccessDenied("Agent is not authorized to access the requested credential pool.")
        pool = self.configurations.pools[pool_id]
        if not pool.enabled:
            raise PoolDisabled("The agent's credential pool is disabled.", details={"pool_id": pool_id.value})
        return self._invoke_pool(pool_id, pool, request)

    def _invoke_pool(self, pool_id: PoolId, pool: PoolConfiguration, request: GatewayRequest):
        input_bytes = sum(len(message.content.encode("utf-8")) for message in request.messages)
        if request.output_schema:
            input_bytes += len(str(request.output_schema).encode("utf-8"))
        input_cap = min(request.max_input_bytes or pool.limits.max_input_bytes_per_request,
                        pool.limits.max_input_bytes_per_request)
        if input_bytes > input_cap:
            raise PoolBudgetExceeded("Pool input-byte budget exceeded.", details={"pool_id": pool_id.value})
        if request.max_output_tokens > min(pool.limits.max_output_tokens_per_request,
                                            self.global_policy.limits.max_output_tokens):
            raise PoolBudgetExceeded("Pool output-token budget exceeded.", details={"pool_id": pool_id.value})

        allowed = []
        paid_blocked = False
        unsupported = False
        tier_blocked = False
        for pool_route in pool.routes:
            route = pool_route.route
            available_capabilities = set(route.capabilities)
            if route.supports_structured_output:
                available_capabilities.add("structured_output")
            if (request.task not in route.supported_tasks or
                    (request.output_schema and not route.supports_structured_output) or
                    not request.required_capabilities.issubset(available_capabilities)):
                unsupported = True
                continue
            if route.tier not in self.global_policy.enabled_tiers:
                paid_blocked = paid_blocked or route.tier == ModelTier.PAID
                tier_blocked = True
                continue
            if route.tier == ModelTier.PAID and not (pool.paid_authorized and self.global_policy.allow_paid):
                paid_blocked = True
                continue
            allowed.append(pool_route)
        if not allowed:
            if paid_blocked:
                raise PolicyDenied("Paid model routes are disabled or unauthorized.")
            if tier_blocked:
                raise PolicyDenied("No model route tier is enabled by gateway policy.")
            if unsupported:
                raise PolicyDenied("No pool route supports the requested task or capability.")
            raise PoolExhausted("The enabled pool has no eligible model routes.", details={"pool_id": pool_id.value})

        candidates = [(pool_route, credential) for pool_route in allowed for credential in pool_route.credentials]
        failures: list[str] = []
        used_quota: set[tuple] = set()
        down_providers: set[str] = set()
        attempts = 0
        last_error = None

        for candidate_index, (pool_route, reference) in enumerate(candidates):
            route = pool_route.route
            quota_key = reference.identity.key
            if quota_key in used_quota or route.provider_id in down_providers:
                continue
            if not self._circuit_available(pool_id, route.provider_id):
                down_providers.add(route.provider_id)
                failures.append(ProviderFailureKind.TEMPORARY_OUTAGE.value)
                continue
            cost_ceiling = self._cost_ceiling(request, pool)
            worst_cost = self._worst_case_cost(route, input_bytes, request.max_output_tokens)
            if cost_ceiling is not None and (worst_cost is None or worst_cost > cost_ceiling):
                failures.append("cost_budget_unenforceable")
                continue
            if pool.limits.max_cost_per_request is not None and (worst_cost is None or worst_cost > pool.limits.max_cost_per_request):
                failures.append("cost_budget_unenforceable")
                continue

            try:
                if self._credential_resolver is None:
                    raise ValueError
                resolved = self._credential_resolver.resolve(reference)
            except Exception:
                raise PoolConfigurationError("A configured pool credential is unavailable.",
                                             details={"pool_id": pool_id.value}) from None
            credential_retry = 0
            while True:
                try:
                    reservation = self._reserve(pool_id, pool, request, quota_key,
                                                input_bytes, request.max_output_tokens)
                except _QuotaBudgetDenied:
                    used_quota.add(quota_key)
                    failures.append("quota_budget_exhausted")
                    break
                acquired_global = self.global_semaphore.acquire(blocking=False)
                if not acquired_global:
                    self._release(reservation)
                    raise PoolBudgetExceeded("Global concurrency budget exhausted.", details={"pool_id": pool_id.value})
                try:
                    provider = self.providers.get(route.provider_id)
                    if provider is None:
                        raise ProviderCallError(ProviderFailureKind.TEMPORARY_OUTAGE)
                    remaining = self._remaining_seconds(request)
                    attempts += 1
                    raw = provider.complete(route, request, timeout_seconds=remaining, credential=resolved)
                    secret_value = resolved.value.get_secret_value()
                    parsed = self._validate_response(raw, request, route, input_bytes, pool, secret_value)
                    self._settle(reservation, parsed.usage.input_tokens, parsed.usage.output_tokens)
                    self._record_circuit_success(pool_id, route.provider_id)
                    usage = parsed.usage.model_copy(update={
                        "cost": self._computed_cost(route, parsed.usage) if self._computed_cost(route, parsed.usage) is not None else parsed.usage.cost,
                        "availability": "available" if parsed.usage.input_tokens is not None and parsed.usage.output_tokens is not None
                        else "partial" if parsed.usage.input_tokens is not None or parsed.usage.output_tokens is not None else "unavailable"})
                    response = GatewayResponse(output=parsed.content, provider_id=route.provider_id,
                        model_id=route.model_id, pool_id=pool_id.value, usage=usage, retries=max(0, attempts - 1))
                    self._emit(request, pool_id, route.provider_id, reference.identity, "completed", attempts)
                    return response
                except ProviderCallError as exc:
                    last_error = exc
                    kind = exc.kind
                    failures.append(kind.value)
                    self._release(reservation)
                    if kind not in _ELIGIBLE or not request.idempotent:
                        if kind in {ProviderFailureKind.TEMPORARY_OUTAGE, ProviderFailureKind.SERVER_TRANSIENT}:
                            self._record_circuit_failure(pool_id, route.provider_id, pool.retry.circuit_breaker_failures,
                                                         pool.retry.circuit_breaker_cooldown_seconds)
                        if kind not in _ELIGIBLE:
                            raise ProviderRejected("Provider rejected the request; fallback is not allowed.",
                                details={"pool_id": pool_id.value, "failure_kind": kind.value}) from None
                        self._exhausted(pool_id, failures, "Request is not marked idempotent; fallback is disabled.")
                    if kind in {ProviderFailureKind.TEMPORARY_OUTAGE, ProviderFailureKind.SERVER_TRANSIENT}:
                        self._record_circuit_failure(pool_id, route.provider_id, pool.retry.circuit_breaker_failures,
                                                     pool.retry.circuit_breaker_cooldown_seconds)
                        down_providers.add(route.provider_id)
                    used_quota.add(quota_key)
                    retry_same = kind == ProviderFailureKind.NETWORK_RETRYABLE
                    if retry_same and credential_retry < pool.retry.max_retries_per_credential:
                        credential_retry += 1
                        delay = self._delay(pool, credential_retry, exc.retry_after_seconds)
                        if delay is None:
                            self._exhausted(pool_id, failures, "Retry-After exceeds the configured safe wait window.")
                        self._wait(request, delay, pool_id, failures)
                        continue
                    next_candidate = any(
                        later_ref.identity.key not in used_quota | {quota_key}
                        and later_route.route.provider_id not in down_providers
                        for later_route, later_ref in candidates[candidate_index + 1:])
                    if next_candidate:
                        delay = self._delay(pool, credential_retry + 1, exc.retry_after_seconds)
                        if delay is None:
                            self._exhausted(pool_id, failures, "Retry delay exceeds the configured safe wait window.")
                        self._wait(request, delay, pool_id, failures)
                    break
                except (PoolBudgetExceeded, GatewayTimeout, InvalidProviderResponse):
                    self._release(reservation)
                    raise
                except Exception:
                    self._release(reservation)
                    # Unknown adapter, schema, auth, and programming errors do not trigger fallback.
                    self._record_circuit_failure(pool_id, route.provider_id,
                                                 pool.retry.circuit_breaker_failures,
                                                 pool.retry.circuit_breaker_cooldown_seconds)
                    raise ProviderRejected("Provider invocation failed permanently; fallback is not allowed.",
                        details={"pool_id": pool_id.value, "failure_kind": ProviderFailureKind.PERMANENT.value}) from None
                finally:
                    self.global_semaphore.release()

        if attempts == 0 and failures and all(item == "cost_budget_unenforceable" for item in failures):
            raise PoolBudgetExceeded("No route can enforce the configured cost ceiling.", details={"pool_id": pool_id.value})
        self._exhausted(pool_id, failures or ([last_error.kind.value] if last_error else []),
                        "No eligible credential and route fallback remains.")

    def _reserve(self, pool_id, pool, request, quota_key, input_tokens, output_tokens):
        now = self._now()
        with self._lock:
            pool_window = self._pool_requests[pool_id]
            quota_window = self._quota_requests[quota_key]
            execution_window = self._execution_requests[(pool_id, request.execution_id)]
            agent_window = self._agent_requests[(pool_id, request.agent_id)]
            self._prune(pool_window, now, 60)
            self._prune(quota_window, now, 60)
            self._prune(execution_window, now, 3600)
            self._prune(agent_window, now, 3600)
            pool_tokens = self._pool_tokens[pool_id]
            quota_tokens = self._quota_tokens[quota_key]
            self._prune_tokens(pool_tokens, now)
            self._prune_tokens(quota_tokens, now)
            quota_limit = self._quota_limits[quota_key]
            if len(pool_window) >= pool.limits.max_requests_per_minute:
                raise PoolBudgetExceeded("Pool request-rate budget exhausted.", details={"pool_id": pool_id.value})
            if len(execution_window) >= pool.limits.max_requests_per_execution:
                raise PoolBudgetExceeded("Pool per-execution request budget exhausted.", details={"pool_id": pool_id.value})
            if len(agent_window) >= pool.limits.max_requests_per_agent_per_hour:
                raise PoolBudgetExceeded("Pool per-agent request budget exhausted.", details={"pool_id": pool_id.value})
            if len(quota_window) >= quota_limit.max_requests_per_minute:
                raise _QuotaBudgetDenied("Shared provider quota bucket request limit exhausted.")
            if self._active_pool[pool_id] >= pool.limits.max_concurrent_requests:
                raise RateLimited("Pool concurrency limit reached.", details={"pool_id": pool_id.value})
            if self._active_quota[quota_key] >= quota_limit.max_concurrent_requests:
                raise _QuotaBudgetDenied("Shared provider quota bucket concurrency exhausted.")
            if sum(entry.input_tokens for entry in pool_tokens) + input_tokens > pool.limits.max_input_tokens_per_hour:
                raise PoolBudgetExceeded("Pool input-token budget exhausted.", details={"pool_id": pool_id.value})
            if sum(entry.output_tokens for entry in pool_tokens) + output_tokens > pool.limits.max_output_tokens_per_hour:
                raise PoolBudgetExceeded("Pool output-token budget exhausted.", details={"pool_id": pool_id.value})
            if sum(entry.input_tokens for entry in quota_tokens) + input_tokens > quota_limit.max_input_tokens_per_hour:
                raise _QuotaBudgetDenied("Shared provider quota bucket input-token budget exhausted.")
            if sum(entry.output_tokens for entry in quota_tokens) + output_tokens > quota_limit.max_output_tokens_per_hour:
                raise _QuotaBudgetDenied("Shared provider quota bucket output-token budget exhausted.")
            pool_window.append(now)
            quota_window.append(now)
            execution_window.append(now)
            agent_window.append(now)
            pool_entry, quota_entry = _TokenEntry(now, input_tokens, output_tokens), _TokenEntry(now, input_tokens, output_tokens)
            pool_tokens.append(pool_entry)
            quota_tokens.append(quota_entry)
            self._active_pool[pool_id] += 1
            self._active_quota[quota_key] += 1
            return _Reservation(pool_id, quota_key, pool_entry, quota_entry)

    def _release(self, reservation):
        with self._lock:
            self._active_pool[reservation.pool_id] = max(0, self._active_pool[reservation.pool_id] - 1)
            self._active_quota[reservation.quota_key] = max(0, self._active_quota[reservation.quota_key] - 1)

    def _settle(self, reservation, input_tokens, output_tokens):
        with self._lock:
            if input_tokens is not None:
                reservation.pool_tokens.input_tokens = input_tokens
                reservation.quota_tokens.input_tokens = input_tokens
            if output_tokens is not None:
                reservation.pool_tokens.output_tokens = output_tokens
                reservation.quota_tokens.output_tokens = output_tokens
            self._active_pool[reservation.pool_id] = max(0, self._active_pool[reservation.pool_id] - 1)
            self._active_quota[reservation.quota_key] = max(0, self._active_quota[reservation.quota_key] - 1)

    @staticmethod
    def _prune(window, now, seconds):
        while window and now - window[0] >= seconds:
            window.popleft()

    @staticmethod
    def _prune_tokens(window, now):
        while window and now - window[0].at >= 3600:
            window.popleft()

    def _validate_response(self, raw, request, route, input_bytes, pool, secret_value):
        try:
            raw_data = raw.model_dump(mode="python") if isinstance(raw, ProviderResponse) else raw
            if isinstance(raw_data, dict):
                raw_data = dict(raw_data)
                raw_data["content"] = self._redact_secret(raw_data.get("content"), secret_value)
                raw_data["metadata"] = self._redact_secret(raw_data.get("metadata", {}), secret_value)
            parsed = ProviderResponse.model_validate(raw_data)
        except Exception:
            raise InvalidProviderResponse("Provider response did not match the response contract.") from None
        self._validate_provider_data(parsed)
        if parsed.usage.output_tokens is not None and parsed.usage.output_tokens > min(request.max_output_tokens,
                                                                                         pool.limits.max_output_tokens_per_request):
            raise InvalidProviderResponse("Provider exceeded the configured output-token limit.")
        if parsed.usage.input_tokens is not None and parsed.usage.input_tokens > min(input_bytes,
                                                                                    pool.limits.max_input_bytes_per_request):
            raise InvalidProviderResponse("Provider exceeded the configured input budget.")
        if request.output_schema is not None:
            if not isinstance(parsed.content, dict):
                raise InvalidProviderResponse("Structured response was not a JSON object.")
            try:
                Draft202012Validator.check_schema(request.output_schema)
                Draft202012Validator(request.output_schema).validate(parsed.content)
            except Exception:
                raise InvalidProviderResponse("Structured response did not match its schema.") from None
        if request.deadline and datetime.now(timezone.utc) >= request.deadline:
            raise GatewayTimeout("Provider response arrived after the request deadline.")
        cost = self._computed_cost(route, parsed.usage)
        ceilings = [v for v in (request.max_cost, pool.limits.max_cost_per_request,
                                self.global_policy.limits.max_cost_per_request) if v is not None]
        if ceilings and cost is not None and cost > min(ceilings):
            raise PoolBudgetExceeded("Pool cost budget exceeded.")
        return parsed

    def _cost_ceiling(self, request, pool):
        values = [v for v in (request.max_cost, pool.limits.max_cost_per_request,
                              self.global_policy.limits.max_cost_per_request) if v is not None]
        return min(values) if values else None

    @staticmethod
    def _worst_case_cost(route, input_bytes, output_tokens):
        if not route.price_known:
            return None
        return input_bytes * route.cost_per_1k_input_tokens / 1000 + output_tokens * route.cost_per_1k_output_tokens / 1000

    @staticmethod
    def _computed_cost(route, usage):
        if not route.price_known or usage.input_tokens is None or usage.output_tokens is None:
            return None
        return usage.input_tokens * route.cost_per_1k_input_tokens / 1000 + usage.output_tokens * route.cost_per_1k_output_tokens / 1000

    def _remaining_seconds(self, request):
        if request.deadline is None:
            return 60.0
        remaining = (request.deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise GatewayTimeout("LLM request deadline elapsed.")
        return min(remaining, 60.0)

    def _delay(self, pool, retry_number, retry_after):
        policy = pool.retry
        if retry_after is not None and retry_after > policy.max_retry_after_seconds:
            return None
        exponential = min(policy.max_backoff_seconds,
                          policy.initial_backoff_seconds * (2 ** max(0, retry_number - 1)))
        jitter = exponential * policy.jitter_fraction * min(1.0, max(0.0, self._random()))
        delay = max(retry_after or 0.0, exponential + jitter)
        if delay > policy.max_backoff_seconds or (retry_after is not None and delay < retry_after):
            return None
        return delay

    def _wait(self, request, delay, pool_id, failures):
        remaining = self._remaining_seconds(request) if request.deadline else 60.0
        if delay > remaining:
            self._exhausted(pool_id, failures, "Retry delay exceeds the request deadline.")
        if delay > 0:
            self._sleep(delay)

    def _circuit_available(self, pool_id, provider_id):
        with self._lock:
            return self._circuit_until[(pool_id, provider_id)] <= self._now()

    def _record_circuit_failure(self, pool_id, provider_id, threshold, cooldown):
        key = (pool_id, provider_id)
        with self._lock:
            self._circuit_failures[key] += 1
            if self._circuit_failures[key] >= threshold:
                self._circuit_until[key] = self._now() + cooldown

    def _record_circuit_success(self, pool_id, provider_id):
        key = (pool_id, provider_id)
        with self._lock:
            self._circuit_failures[key] = 0
            self._circuit_until[key] = 0

    def _exhausted(self, pool_id, failures, message):
        safe_failures = tuple(dict.fromkeys(str(item) for item in failures))
        raise PoolExhausted(message, details={"pool_id": pool_id.value, "failure_kinds": safe_failures})

    @staticmethod
    def _validate_provider_data(response):
        def visit(value, depth=0):
            if depth > 12:
                raise InvalidProviderResponse("Provider metadata exceeded limits.")
            if isinstance(value, dict):
                for key, nested in value.items():
                    if any(part in str(key).casefold() for part in _SECRET_METADATA_KEYS):
                        raise InvalidProviderResponse("Provider metadata contains sensitive fields.")
                    visit(nested, depth + 1)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested, depth + 1)
        visit(response.metadata)
        if len(str(response.content).encode("utf-8")) > 1_000_000:
            raise InvalidProviderResponse("Provider response exceeded size limit.")

    @staticmethod
    def _redact_secret(value, secret):
        if isinstance(value, str):
            return value.replace(secret, "[REDACTED]") if secret else value
        if isinstance(value, dict):
            return {PoolCredentialRouter._redact_secret(str(key), secret):
                    PoolCredentialRouter._redact_secret(nested, secret) for key, nested in value.items()}
        if isinstance(value, list):
            return [PoolCredentialRouter._redact_secret(item, secret) for item in value]
        if isinstance(value, tuple):
            return tuple(PoolCredentialRouter._redact_secret(item, secret) for item in value)
        return value

    def _emit(self, request, pool_id, provider_id, identity: QuotaIdentity, status, attempts):
        if not self.observer:
            return
        event = {"pool_id": pool_id.value, "agent_id": request.agent_id, "execution_id": request.execution_id,
                 "correlation_id": request.correlation_id, "trace_id": request.trace_id,
                 "provider_id": provider_id, "quota_bucket_id": identity.quota_bucket_id,
                 "status": status, "attempts": attempts}
        try: self.observer(event)
        except Exception: pass


class _QuotaBudgetDenied(Exception):
    """Internal signal that permits trying another declared quota identity."""
    pass
