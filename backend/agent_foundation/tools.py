"""Explicit tool registry and mediated, bounded tool invocation."""

import concurrent.futures
import json
import time
import uuid

from pydantic import BaseModel, ValidationError

from .contracts.tools import ToolContract, ToolResult, ToolCategory
from .errors import DuplicateAgentError, PermissionDeniedError, ToolExecutionError, ToolTimeoutError, UnsupportedCapabilityError
from .permissions import require_permissions
from .validation import _safe_errors


class ToolDefinition:
    def __init__(self, contract, input_model, output_model, handler):
        if not isinstance(contract, ToolContract): raise TypeError("contract must be a ToolContract")
        if not isinstance(input_model, type) or not issubclass(input_model, BaseModel): raise TypeError("input_model must be a Pydantic model class")
        if not isinstance(output_model, type) or not issubclass(output_model, BaseModel): raise TypeError("output_model must be a Pydantic model class")
        if not callable(handler): raise TypeError("handler must be callable")
        self.contract, self.input_model, self.output_model, self.handler = contract, input_model, output_model, handler


class ToolRegistry:
    def __init__(self): self._tools = {}

    def register(self, definition):
        if not isinstance(definition, ToolDefinition): raise TypeError("definition must be ToolDefinition")
        if definition.contract.tool_id in self._tools:
            raise DuplicateAgentError("A tool with this stable ID is already registered.")
        if definition.contract.category == ToolCategory.AGENT_PRIVATE and not definition.contract.owner_agent_id:
            raise ValueError("agent-private tools require owner_agent_id")
        if definition.contract.category == ToolCategory.CONTROLLED_EXECUTION and not definition.contract.sandbox_required:
            raise ValueError("controlled execution tools require a sandbox boundary")
        self._tools[definition.contract.tool_id] = definition

    def resolve(self, tool_id):
        try: return self._tools[tool_id]
        except KeyError as exc: raise UnsupportedCapabilityError("Tool is not registered.") from exc


def invoke_tool(registry, tool_id, arguments, *, context, agent_id=None, sandbox=None, observer=None):
    definition = registry.resolve(tool_id)
    contract = definition.contract
    if contract.owner_agent_id and contract.owner_agent_id != agent_id:
        raise PermissionDeniedError("This agent does not own the requested tool.")
    require_permissions(context.permissions, contract.required_permissions)
    if contract.sandbox_required and sandbox is None:
        raise UnsupportedCapabilityError("This tool requires an approved sandbox executor.")
    try:
        parsed = definition.input_model.model_validate(arguments)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ToolExecutionError("Tool arguments do not match the registered contract.", details={"errors": _safe_errors(exc) if isinstance(exc, ValidationError) else []}) from exc
    started = time.monotonic()
    invocation_id = str(uuid.uuid4())
    try:
        def call():
            context.check_active()
            return sandbox.invoke(contract, parsed, context) if contract.sandbox_required else definition.handler(parsed, context)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(call)
        try: output = future.result(timeout=contract.timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise ToolTimeoutError("Tool exceeded its execution time limit.") from exc
        else:
            pool.shutdown(wait=True)
        output = definition.output_model.model_validate(output)
        serialized = output.model_dump_json().encode("utf-8")
        if len(serialized) > contract.max_output_bytes:
            raise ToolExecutionError("Tool result exceeded its response limit.")
        result = ToolResult(tool_id=tool_id, status="succeeded", output=output,
                            duration_ms=int((time.monotonic()-started)*1000), invocation_id=invocation_id)
        if observer:
            try:
                observer.tool_invoked(execution_id=context.execution_id, agent_id=context.agent_id,
                    agent_version="unknown", correlation_id=context.correlation_id,
                    trace_id=context.trace_id, result=result)
            except Exception:
                pass
        return result
    except ToolTimeoutError: raise
    except ToolExecutionError: raise
    except Exception as exc:
        raise ToolExecutionError("Tool invocation failed.", details={"error_type": type(exc).__name__}) from exc
