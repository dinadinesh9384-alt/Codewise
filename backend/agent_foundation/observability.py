"""Structured, bounded, content-safe execution events."""

from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class ObservabilityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    execution_id: str
    agent_id: str
    agent_version: str
    status: str
    correlation_id: str
    trace_id: str
    duration_ms: int = Field(ge=0)
    error_categories: tuple[str, ...] = ()
    tool_id: str | None = None
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    retries: int = Field(default=0, ge=0)


class EventSink(Protocol):
    def emit(self, event: ObservabilityEvent) -> None: ...


class ExecutionObserver:
    def __init__(self, sink: EventSink): self.sink = sink

    def execution_finished(self, result):
        self.sink.emit(ObservabilityEvent(event_type="execution_finished", execution_id=str(result.execution_id),
            agent_id=result.agent_id, agent_version=result.agent_version, status=result.status.value,
            correlation_id=result.correlation_id, trace_id=result.trace_id,
            duration_ms=result.timing.duration_ms, error_categories=tuple(error.code for error in result.errors),
            provider=result.model_usage.provider if result.model_usage else None,
            model=result.model_usage.model if result.model_usage else None,
            input_tokens=result.model_usage.input_tokens if result.model_usage else None,
            output_tokens=result.model_usage.output_tokens if result.model_usage else None,
            cost=result.model_usage.cost if result.model_usage else None,
            retries=result.model_usage.retries if result.model_usage else 0))

    def tool_invoked(self, *, execution_id, agent_id, agent_version, correlation_id, trace_id, result):
        self.sink.emit(ObservabilityEvent(event_type="tool_invoked", execution_id=str(execution_id),
            agent_id=agent_id, agent_version=agent_version, status=result.status,
            correlation_id=correlation_id, trace_id=trace_id, duration_ms=result.duration_ms,
            tool_id=result.tool_id, error_categories=(result.error_code,) if result.error_code else ()))
