"""Native LangChain integration whose only model transport is ``llm_gateway``."""

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import ConfigDict, Field, PrivateAttr, field_validator

from ..errors import LLMGatewayUnavailableError
from ..permissions import Permission, require_permissions
from llm_gateway.contracts import GatewayRequest, LLMMessage


def _content(message):
    value = message.content
    if isinstance(value, str): return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _role(message):
    if isinstance(message, SystemMessage): return "system"
    if isinstance(message, HumanMessage): return "user"
    if isinstance(message, ToolMessage): return "tool"
    return "assistant"


class GatewayChatModel(BaseChatModel):
    """A usable LangChain chat model with no provider SDK or direct network path."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    permissions: frozenset[Permission]
    agent_id: str
    pool_id: str | None = None
    execution_id: str
    correlation_id: str
    trace_id: str
    provider_id: str | None = None
    model_id: str | None = None
    max_output_tokens: int = Field(default=1024, ge=1, le=32768)
    task: str = "analysis"
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    seed: int | None = None
    required_capabilities: frozenset[str] = frozenset()
    idempotent: bool = False
    _usage_records: list = PrivateAttr(default_factory=list)
    _gateway: Any = PrivateAttr(default=None)

    def __init__(self, *, gateway, **data):
        super().__init__(**data)
        self._gateway = gateway

    @property
    def is_configured(self):
        from llm_gateway.gateway import LLMGateway
        return isinstance(self._gateway, LLMGateway)

    @field_validator("execution_id", mode="before")
    @classmethod
    def normalize_execution_id(cls, value):
        return str(value)

    @property
    def usage_summary(self):
        if not self._usage_records: return None
        return {"provider": self._usage_records[-1][0], "model": self._usage_records[-1][1],
                "input_tokens": self._usage_records[-1][2], "output_tokens": self._usage_records[-1][3],
                "cost": self._usage_records[-1][4], "retries": self._usage_records[-1][5]}

    @property
    def _llm_type(self) -> str:
        return "codewise_gateway"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id}

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        require_permissions(self.permissions, {Permission.LLM_INVOKE})
        if not self.is_configured:
            raise LLMGatewayUnavailableError("LLM gateway is not configured.")
        unsupported = set(kwargs) - {"temperature", "top_p", "seed"}
        if unsupported:
            raise ValueError("Unsupported model options.")
        request = GatewayRequest(agent_id=self.agent_id, pool_id=self.pool_id, execution_id=self.execution_id,
            provider_id=self.provider_id, model_id=self.model_id,
            messages=tuple(LLMMessage(role=_role(m), content=_content(m)) for m in messages),
            correlation_id=self.correlation_id, trace_id=self.trace_id, task=self.task,
            stop=tuple(stop or ()), temperature=kwargs.get("temperature", self.temperature),
            top_p=kwargs.get("top_p", self.top_p), seed=kwargs.get("seed", self.seed),
            required_capabilities=self.required_capabilities,
            idempotent=self.idempotent,
            max_output_tokens=self.max_output_tokens)
        response = self._gateway.invoke(request, permissions=self.permissions)
        self._usage_records.append((response.provider_id, response.model_id, response.usage.input_tokens,
                                    response.usage.output_tokens, response.usage.cost, response.retries))
        content = response.output if isinstance(response.output, str) else json.dumps(response.output, ensure_ascii=False)
        metadata = {"provider_id": response.provider_id, "model_id": response.model_id,
                    "pool_id": response.pool_id,
                    "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
                    "cost": response.usage.cost, "usage_availability": response.usage.availability,
                    "retries": response.retries}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content, response_metadata=metadata))])

    def with_max_output_tokens(self, max_output_tokens: int) -> "GatewayChatModel":
        """Return a copy of this model with a different max_output_tokens.

        Construct a new model through __init__ so the gateway dependency is
        forwarded explicitly while every declared configuration field is retained.
        """
        field_values = {name: getattr(self, name) for name in self.model_fields}
        field_values["max_output_tokens"] = max_output_tokens
        return GatewayChatModel(gateway=self._gateway, **field_values)

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs):
        if kwargs:
            raise ValueError("Unsupported structured-output options.")
        if isinstance(schema, type) and hasattr(schema, "model_json_schema"):
            json_schema = schema.model_json_schema()
            parser = schema.model_validate
        elif isinstance(schema, dict):
            json_schema = schema
            parser = lambda value: value
        else:
            raise TypeError("schema must be a Pydantic model or JSON Schema object")

        def invoke(value):
            messages = value.to_messages() if hasattr(value, "to_messages") else [HumanMessage(content=value)] if isinstance(value, str) else value
            require_permissions(self.permissions, {Permission.LLM_INVOKE})
            if not self.is_configured:
                raise LLMGatewayUnavailableError("LLM gateway is not configured.")
            request = GatewayRequest(agent_id=self.agent_id, pool_id=self.pool_id, execution_id=self.execution_id,
                provider_id=self.provider_id, model_id=self.model_id,
                messages=tuple(LLMMessage(role=_role(m), content=_content(m)) for m in messages),
                output_schema=json_schema, correlation_id=self.correlation_id, trace_id=self.trace_id,
                task=self.task, temperature=self.temperature, top_p=self.top_p, seed=self.seed,
                required_capabilities=self.required_capabilities,
                idempotent=self.idempotent,
                max_output_tokens=self.max_output_tokens)
            result = self._gateway.invoke(request, permissions=self.permissions)
            self._usage_records.append((result.provider_id, result.model_id, result.usage.input_tokens,
                                        result.usage.output_tokens, result.usage.cost, result.retries))
            parsed = parser(result.output)
            raw = AIMessage(content=json.dumps(result.output, ensure_ascii=False),
                response_metadata={"provider_id": result.provider_id, "model_id": result.model_id,
                    "pool_id": result.pool_id,
                    "usage_availability": result.usage.availability, "retries": result.retries})
            return {"raw": raw, "parsed": parsed} if include_raw else parsed

        return RunnableLambda(invoke)


def to_langchain_tool(tool_registry, tool_id, *, context):
    """Expose an existing typed Foundation tool, preserving its permission boundary."""
    from langchain_core.tools import StructuredTool
    definition = tool_registry.resolve(tool_id)
    def run(**values):
        from ..tools import invoke_tool
        result = invoke_tool(tool_registry, tool_id, values, context=context)
        return result.output.model_dump(mode="json")
    return StructuredTool.from_function(run, name=definition.contract.name,
        description=definition.contract.description, args_schema=definition.input_model)
