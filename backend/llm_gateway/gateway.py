"""Central model routing, policy, budget, validation, and telemetry boundary."""

import threading
import time
import os
from collections import defaultdict, deque
from datetime import datetime, timezone

from jsonschema import Draft202012Validator

from agent_foundation.permissions import Permission, require_permissions

from .contracts import GatewayRequest, GatewayResponse, ModelRoute, ModelTier, ProviderResponse, Usage
from .errors import (BudgetExceeded, GatewayTimeout, InvalidProviderResponse, PolicyDenied,
                     InvalidGatewayRequest, ProviderFailure, ProviderUnavailable, RateLimited, UnknownModel)
from .policy import GatewayPolicy

OFFICIAL_LLM_AGENTS = frozenset({"business_logic_analyst", "code_quality_analyst", "security_analyst",
                                 "migration_planner", "patch_author", "test_engineer", "review_agent"})
_SENSITIVE_KEYS = ("token", "secret", "password", "credential", "authorization", "api_key")


class LLMGateway:
    """In-memory enforcement is process-local. No adapter is registered by default."""
    def __init__(self, *, policy: GatewayPolicy | None = None, routes=(), providers=None, observer=None,
                 pool_configurations=None, credential_resolver=None, pool_sleeper=time.sleep,
                 pool_random_value=None):
        self.policy = policy or GatewayPolicy()
        configured_routes = tuple(routes)
        self.routes = {(r.provider_id, r.model_id): r for r in configured_routes}
        if len(self.routes) != len(configured_routes):
            raise ValueError("Duplicate model route configuration.")
        self.providers = dict(providers or {})
        for route in configured_routes:
            adapter = self.providers.get(route.provider_id)
            validate_route = getattr(adapter, "validate_route", None)
            if callable(validate_route):
                validate_route(route)
        self.observer = observer
        self._lock = threading.RLock()
        self._semaphore = threading.BoundedSemaphore(self.policy.limits.max_concurrent_requests)
        self._times = defaultdict(deque)
        self._execution_requests = defaultdict(deque)
        self._agent_requests = defaultdict(deque)
        self._reservation_count = 0
        self._failures = defaultdict(int)
        self._open_until = defaultdict(float)
        self._pool_router = None
        if pool_configurations is not None:
            from .pool_router import PoolCredentialRouter
            options = {"configurations": pool_configurations, "credential_resolver": credential_resolver,
                       "providers": self.providers, "global_policy": self.policy,
                       "global_semaphore": self._semaphore, "observer": observer, "sleeper": pool_sleeper}
            if pool_random_value is not None:
                options["random_value"] = pool_random_value
            self._pool_router = PoolCredentialRouter(**options)

    @classmethod
    def from_environment(cls, *, providers, policy=None, environ=None, observer=None,
                         pool_sleeper=time.sleep, pool_random_value=None):
        """Load secret-free pool configuration and resolve secret refs from backend environment only."""
        from .credentials import EnvironmentCredentialResolver
        from .pool_contracts import PoolConfigurations
        environment = os.environ if environ is None else environ
        configurations = PoolConfigurations.from_environment(environment)
        return cls(policy=policy, providers=providers, observer=observer,
            pool_configurations=configurations, credential_resolver=EnvironmentCredentialResolver(environment),
            pool_sleeper=pool_sleeper, pool_random_value=pool_random_value)

    def invoke(self, request: GatewayRequest, *, permissions):
        if self._pool_router is not None:
            return self._pool_router.invoke(request, permissions=permissions)
        require_permissions(permissions, {Permission.LLM_INVOKE})
        if not isinstance(request, GatewayRequest):
            raise InvalidGatewayRequest("LLM request did not match the gateway contract.")
        if request.agent_id in OFFICIAL_LLM_AGENTS:
            raise PolicyDenied("Official agents require an enabled, agent-bound credential pool.")
        route = self._choose_route(request)
        self._validate_request_policy(request, route)
        reserved_bytes = sum(len(m.content.encode("utf-8")) for m in request.messages)
        if request.output_schema:
            reserved_bytes += len(str(request.output_schema).encode("utf-8"))
        cap = min(request.max_input_bytes or self.policy.limits.max_input_bytes,
                  self.policy.limits.max_input_bytes)
        if reserved_bytes > cap:
            raise BudgetExceeded("LLM input size exceeds the configured limit.")
        if request.max_cost is not None and not route.price_known:
            raise BudgetExceeded("A cost ceiling cannot be enforced for a route with unknown pricing.")
        if self.policy.limits.max_cost_per_request is not None and not route.price_known:
            raise BudgetExceeded("Configured request cost budget cannot be enforced for this route.")
        ceilings = [v for v in (request.max_cost, self.policy.limits.max_cost_per_request) if v is not None]
        if ceilings:
            worst_case = (reserved_bytes * route.cost_per_1k_input_tokens / 1000 +
                          request.max_output_tokens * route.cost_per_1k_output_tokens / 1000)
            if worst_case > min(ceilings):
                raise BudgetExceeded("Worst-case LLM request cost exceeds its configured budget.")
        self._reserve(request, route)
        if not self._semaphore.acquire(blocking=False):
            self._release(request, route, failed=False)
            raise RateLimited("LLM concurrent request limit reached.")
        started = time.monotonic()
        try:
            timeout = self._remaining_seconds(request)
            provider = self.providers.get(route.provider_id)
            if provider is None:
                raise ProviderUnavailable("Configured provider adapter is unavailable.")
            attempts = self.policy.max_retries + 1 if request.idempotent else 1
            response = None
            for attempt in range(attempts):
                try:
                    response = provider.complete(route, request, timeout_seconds=timeout)
                    break
                except Exception as exc:
                    # No exception text is propagated. Retry only explicitly idempotent requests.
                    if attempt + 1 >= attempts or not bool(getattr(exc, "retryable", False)):
                        raise ProviderFailure("LLM provider request failed.") from None
                    timeout = self._remaining_seconds(request)
            try:
                parsed = ProviderResponse.model_validate(response)
            except Exception:
                raise InvalidProviderResponse("LLM provider response did not match the response contract.") from None
            self._validate_provider_data(parsed)
            if parsed.usage.output_tokens is not None and parsed.usage.output_tokens > request.max_output_tokens:
                raise InvalidProviderResponse("LLM provider exceeded the requested output-token limit.")
            if parsed.usage.input_tokens is not None and parsed.usage.input_tokens > cap:
                raise InvalidProviderResponse("LLM provider exceeded the configured input budget.")
            output = parsed.content
            if request.output_schema is not None:
                if not isinstance(output, dict):
                    raise InvalidProviderResponse("Structured LLM response was not a JSON object.")
                try:
                    Draft202012Validator.check_schema(request.output_schema)
                    Draft202012Validator(request.output_schema).validate(output)
                except Exception:
                    raise InvalidProviderResponse("Structured LLM response did not match its schema.") from None
            estimated_cost = self._cost(route, parsed.usage)
            ceiling = min(v for v in (request.max_cost, self.policy.limits.max_cost_per_request) if v is not None) \
                if any(v is not None for v in (request.max_cost, self.policy.limits.max_cost_per_request)) else None
            if ceiling is not None and estimated_cost is not None and estimated_cost > ceiling:
                raise BudgetExceeded("LLM request exceeded its configured cost budget.")
            if time.monotonic() - started > self._timeout_seconds(request):
                raise GatewayTimeout("LLM request exceeded its deadline.")
            self._release(request, route, failed=False)
            usage = parsed.usage.model_copy(update={"cost": estimated_cost if estimated_cost is not None else parsed.usage.cost,
                "availability": "available" if parsed.usage.input_tokens is not None and parsed.usage.output_tokens is not None
                else "partial" if parsed.usage.input_tokens is not None or parsed.usage.output_tokens is not None else "unavailable"})
            result = GatewayResponse(output=output, provider_id=route.provider_id, model_id=route.model_id,
                                     usage=usage, retries=attempt)
            self._emit(request, route, "completed", usage)
            return result
        except (ProviderUnavailable, ProviderFailure, InvalidProviderResponse, GatewayTimeout, BudgetExceeded):
            self._release(request, route, failed=True)
            self._emit(request, route, "failed", None)
            raise
        finally:
            self._semaphore.release()

    def _choose_route(self, request):
        if request.provider_id is not None:
            route = self.routes.get((request.provider_id, request.model_id))
            if route is None:
                raise UnknownModel("Requested model route is not registered.")
            return route
        candidates = [route for route in self.routes.values() if route.tier in self.policy.enabled_tiers]
        # Never silently fall back; model selection must be explicit and deterministic.
        if len(candidates) != 1:
            raise UnknownModel("No unique model route is configured for this request.")
        return candidates[0]

    def _validate_request_policy(self, request, route):
        if not self.policy.enabled or request.agent_id not in OFFICIAL_LLM_AGENTS or request.agent_id not in self.policy.allowed_agents:
            raise PolicyDenied("LLM invocation is not enabled for this agent.")
        if (route.provider_id, route.model_id) not in self.policy.allowed_models:
            raise PolicyDenied("Selected model is not allow-listed.")
        if route.tier not in self.policy.enabled_tiers:
            raise PolicyDenied("Selected model tier is disabled.")
        if request.task not in route.supported_tasks:
            raise PolicyDenied("Selected model route does not support this task.")
        available_capabilities = set(route.capabilities)
        if route.supports_structured_output:
            available_capabilities.add("structured_output")
        if not request.required_capabilities.issubset(available_capabilities):
            raise PolicyDenied("Selected model route lacks a required capability.")
        if route.tier == ModelTier.PAID and not self.policy.allow_paid:
            raise PolicyDenied("Paid model use is disabled.")
        if request.output_schema and not route.supports_structured_output:
            raise PolicyDenied("Selected route does not support structured output.")
        if request.max_output_tokens > self.policy.limits.max_output_tokens:
            raise BudgetExceeded("Requested output token limit exceeds the configured limit.")
        with self._lock:
            now = time.monotonic()
            if self._open_until[route.provider_id] > now:
                raise ProviderUnavailable("Provider circuit is open after recent failures.")

    def _reserve(self, request, route):
        now = time.monotonic()
        with self._lock:
            window = self._times[request.agent_id]
            while window and now - window[0] >= 60:
                window.popleft()
            execution_window = self._execution_requests[request.execution_id]
            while execution_window and now - execution_window[0] >= 3600:
                execution_window.popleft()
            agent_window = self._agent_requests[request.agent_id]
            while agent_window and now - agent_window[0] >= 3600:
                agent_window.popleft()
            if len(window) >= self.policy.limits.max_requests_per_minute:
                raise RateLimited("LLM request rate limit reached.")
            if len(execution_window) >= self.policy.limits.max_requests_per_execution:
                raise BudgetExceeded("LLM execution request budget exhausted.")
            if len(agent_window) >= self.policy.limits.max_requests_per_agent:
                raise BudgetExceeded("LLM agent request budget exhausted.")
            window.append(now)
            execution_window.append(now)
            agent_window.append(now)
            self._reservation_count += 1
            if self._reservation_count % 1000 == 0:
                expired = [key for key, timestamps in self._execution_requests.items()
                           if not timestamps or now - timestamps[-1] >= 3600]
                for key in expired: self._execution_requests.pop(key, None)

    def _release(self, request, route, *, failed):
        with self._lock:
            if failed:
                self._failures[route.provider_id] += 1
                if self._failures[route.provider_id] >= self.policy.circuit_breaker_failures:
                    self._open_until[route.provider_id] = time.monotonic() + self.policy.circuit_breaker_cooldown_seconds
            else:
                self._failures[route.provider_id] = 0

    def _remaining_seconds(self, request):
        if request.deadline is None:
            return 60.0
        seconds = (request.deadline - datetime.now(timezone.utc)).total_seconds()
        if seconds <= 0:
            raise GatewayTimeout("LLM request deadline elapsed.")
        return min(seconds, 60.0)

    def _timeout_seconds(self, request):
        return self._remaining_seconds(request)

    @staticmethod
    def _cost(route, usage):
        if not route.price_known or usage.input_tokens is None or usage.output_tokens is None:
            return None
        return usage.input_tokens * route.cost_per_1k_input_tokens / 1000 + usage.output_tokens * route.cost_per_1k_output_tokens / 1000

    @staticmethod
    def _validate_provider_data(response):
        def visit(obj, depth=0):
            if depth > 12:
                raise InvalidProviderResponse("Provider response metadata exceeded limits.")
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if any(part in str(key).casefold() for part in _SENSITIVE_KEYS):
                        raise InvalidProviderResponse("Provider response contains sensitive metadata.")
                    visit(value, depth + 1)
            elif isinstance(obj, (list, tuple)):
                for value in obj: visit(value, depth + 1)
        visit(response.metadata)
        if len(str(response.content).encode("utf-8")) > 1_000_000:
            raise InvalidProviderResponse("Provider response exceeded size limit.")

    def _emit(self, request, route, status, usage):
        if not self.observer:
            return
        event = {"execution_id": request.execution_id, "agent_id": request.agent_id,
                 "correlation_id": request.correlation_id, "trace_id": request.trace_id,
                 "provider_id": route.provider_id, "model_id": route.model_id, "status": status,
                 "input_tokens": usage.input_tokens if usage else None,
                 "output_tokens": usage.output_tokens if usage else None,
                 "cost": usage.cost if usage else None}
        try: self.observer(event)
        except Exception: pass
