"""In-memory agent lifecycle coordinator with validation and least privilege."""

import threading
import time
import inspect
from datetime import datetime, timezone

from .context import ExecutionContext
from .contracts.agent import AgentCapability
from .contracts.execution import ExecutionStatus, ExecutionRequest, TimingMetadata, ValidationStatus
from .errors import AgentTimeoutError, ExecutionCancelledError, LLMGatewayUnavailableError
from .permissions import require_permissions
from .registry import AgentRegistry
from .results import ExecutionResult
from .validation import validate_input, validate_output

_ALLOWED = {
    ExecutionStatus.CREATED: {ExecutionStatus.VALIDATING, ExecutionStatus.CANCELLED},
    ExecutionStatus.VALIDATING: {ExecutionStatus.QUEUED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED},
    ExecutionStatus.QUEUED: {ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED, ExecutionStatus.TIMED_OUT},
    ExecutionStatus.RUNNING: {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED, ExecutionStatus.TIMED_OUT},
    ExecutionStatus.COMPLETED: set(), ExecutionStatus.FAILED: set(), ExecutionStatus.CANCELLED: set(), ExecutionStatus.TIMED_OUT: set(),
}


class ExecutionLifecycle:
    def __init__(self, registry: AgentRegistry, *, observer=None, chat_model_factory=None):
        self.registry = registry
        self.observer = observer
        self.chat_model_factory = chat_model_factory

    @staticmethod
    def transition(current, target):
        if target not in _ALLOWED[current]:
            from .errors import InvalidExecutionTransitionError
            raise InvalidExecutionTransitionError("Execution state transition is not allowed.", details={"from": current.value, "to": target.value})
        return target

    def execute(self, request: ExecutionRequest, context: ExecutionContext):
        definition = self.registry.resolve(request.agent_id)
        started = datetime.now(timezone.utc)
        began = time.monotonic()
        state = ExecutionStatus.CREATED
        validated = ValidationStatus.NOT_RUN
        try:
            if request.agent_version != definition.contract.version:
                from .errors import InvalidInputError
                raise InvalidInputError("Requested agent version is not registered.")
            if context.execution_id != request.execution_id or context.agent_id != request.agent_id:
                from .errors import InvalidInputError
                raise InvalidInputError("Execution context does not match the request.")
            if (context.workspace_id != request.workspace_id or context.repository_id != request.repository_id
                    or context.snapshot_id != request.snapshot_id or context.commit_sha != request.commit_sha
                    or context.manifest_digest != request.manifest_digest):
                from .errors import InvalidInputError
                raise InvalidInputError("Execution context is not bound to the requested repository snapshot.")
            if request.mode not in {mode.value for mode in definition.contract.supported_modes}:
                from .errors import UnsupportedCapabilityError
                raise UnsupportedCapabilityError("Requested execution mode is not supported by this agent.")
            state = self.transition(state, ExecutionStatus.VALIDATING)
            inp = validate_input(definition.input_model, request.input)
            validated = ValidationStatus.PASSED
            required = set(definition.contract.required_permissions) | set(request.requested_capabilities)
            if request.agent_id == "structure_analyzer" and "llm.invoke" in (required | set(context.permissions)):
                from .errors import PermissionDeniedError
                raise PermissionDeniedError("structure_analyzer cannot receive LLM access.")
            require_permissions(context.permissions, required)
            context.check_active()
            state = self.transition(state, ExecutionStatus.QUEUED)
            state = self.transition(state, ExecutionStatus.RUNNING)
            model = None
            if definition.contract.requires_llm:
                require_permissions(context.permissions, {"llm.invoke"})
                if self.chat_model_factory is None:
                    raise LLMGatewayUnavailableError("LLM gateway is not configured.")
                model = self.chat_model_factory(definition, request, context)
                from .adapters.langchain_adapter import GatewayChatModel
                if not isinstance(model, GatewayChatModel) or not model.is_configured:
                    raise LLMGatewayUnavailableError("LLM agents require the configured gateway chat model.")
                try:
                    parameters = tuple(inspect.signature(definition.runner).parameters.values())
                    accepts_model = len(parameters) >= 3 or any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in parameters)
                except (TypeError, ValueError):
                    accepts_model = False
                if not accepts_model:
                    from .errors import UnsupportedCapabilityError
                    raise UnsupportedCapabilityError("LLM agent runner must accept its gateway chat model.")
                output = definition.runner(inp, context, model)
            else:
                output = definition.runner(inp, context)
            context.check_active()
            parsed = validate_output(definition.output_model, output)
            state = self.transition(state, ExecutionStatus.COMPLETED)
            return self._result(request, definition, state, parsed, validated, started, began, model=model)
        except ExecutionCancelledError as exc:
            state = ExecutionStatus.CANCELLED
            return self._failure(request, definition, state, exc, validated, started, began)
        except AgentTimeoutError as exc:
            state = ExecutionStatus.TIMED_OUT
            return self._failure(request, definition, state, exc, validated, started, began)
        except Exception as exc:
            state = ExecutionStatus.FAILED
            return self._failure(request, definition, state, exc, validated, started, began)

    def _result(self, request, definition, state, output, validation, started, began, *, model=None):
        from .contracts.execution import ModelUsage
        usage = ModelUsage(**model.usage_summary) if model is not None and model.usage_summary else None
        result = ExecutionResult(execution_id=request.execution_id, agent_id=request.agent_id,
            agent_version=definition.contract.version, status=state, output=output,
            findings=getattr(output, "findings", ()), evidence=getattr(output, "evidence", ()),
            validation_status=validation, model_usage=usage, timing=TimingMetadata(started_at=started,
                completed_at=datetime.now(timezone.utc), duration_ms=int((time.monotonic()-began)*1000)),
            correlation_id=request.correlation_id, trace_id=request.trace_id)
        self._emit(result)
        return result

    def _failure(self, request, definition, state, exc, validation, started, began):
        from .errors import AgentFoundationError
        safe = exc if isinstance(exc, AgentFoundationError) else AgentFoundationError("Agent execution failed.")
        result = ExecutionResult(execution_id=request.execution_id, agent_id=request.agent_id,
            agent_version=definition.contract.version, status=state, validation_status=validation,
            errors=(safe.as_contract(),), timing=TimingMetadata(started_at=started,
                completed_at=datetime.now(timezone.utc), duration_ms=int((time.monotonic()-began)*1000)),
            correlation_id=request.correlation_id, trace_id=request.trace_id)
        self._emit(result)
        return result

    def _emit(self, result):
        if self.observer:
            try:
                self.observer.execution_finished(result)
            except Exception:
                # Telemetry sink outages must not rewrite the agent's execution outcome.
                pass
