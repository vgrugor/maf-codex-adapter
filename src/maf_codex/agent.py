"""A minimal MAF agent backed by local Codex SDK."""

from collections.abc import Awaitable, Sequence
from typing import Any, Literal, overload
from uuid import uuid4

from agent_framework import (
    AgentMiddlewareLayer,
    AgentResponse,
    AgentResponseUpdate,
    AgentSession,
    BaseAgent,
    Content,
    ContextProvider,
    FunctionTool,
    Message,
    ResponseStream,
    SessionContext,
)

from .config import CodexAgentConfig
from .errors import SessionBindingError
from .mapping import records_to_prompt, to_records
from .metadata import counter_delta
from .providers import agent_middleware_only, invocation, set_response, validate_providers
from .runtime import CodexRuntime
from .sessions import bind, new_records, session_lock, validate_binding
from .tools import ApprovalHandler, ToolBridge, tool_fingerprint, validate_tools


class _CodexAgentCore(BaseAgent):
    """Text-only custom agent with explicit incremental/full history modes.

    Use stable ``id`` and unique workflow names. Buffered streaming emits
    one update after completion. MAF context providers and tool hooks are not wired.
    """

    service_session_state_keys = ("maf_codex",)

    def __init__(
        self,
        config: CodexAgentConfig,
        *,
        id: str | None = None,
        name: str = "Codex",
        description: str | None = None,
        runtime: CodexRuntime | None = None,
        middleware: Sequence[Any] | None = None,
        context_providers: Sequence[ContextProvider] | None = None,
        tools: Sequence[FunctionTool] | None = None,
        approval_handler: ApprovalHandler | None = None,
    ):
        super().__init__(
            id=id,
            name=name,
            description=description,
            middleware=middleware,
            context_providers=context_providers,
        )
        self.config = config
        self._runtime = runtime or CodexRuntime(config)
        self.tools = validate_tools(tools)
        self.approval_handler = approval_handler

    def get_session(
        self, service_session_id: str, *, session_id: str | None = None
    ) -> AgentSession:
        """Resume a trusted Codex thread id in incremental mode without replaying history."""
        if not isinstance(service_session_id, str) or not service_session_id.strip():
            raise SessionBindingError("A non-empty Codex thread id is required")
        if self.config.history_mode == "full":
            raise SessionBindingError(
                "Full mode needs a serialized session with acknowledged history"
            )
        if self.tools:
            raise SessionBindingError(
                "Tool-enabled threads require a serialized session with catalog binding"
            )
        session = self.create_session(session_id=session_id)
        session.service_session_id = service_session_id
        session.state["maf_codex"] = {
            self.id: bind(service_session_id, self.config.fingerprint, "incremental", None)
        }
        return session

    @overload
    def run(
        self,
        messages: str | Message | Sequence[str | Message] | None = None,
        *,
        stream: Literal[False] = False,
        session: AgentSession | None = None,
        **kwargs: Any,
    ) -> Awaitable[AgentResponse]: ...

    @overload
    def run(
        self,
        messages: str | Message | Sequence[str | Message] | None = None,
        *,
        stream: Literal[True],
        session: AgentSession | None = None,
        **kwargs: Any,
    ) -> ResponseStream[AgentResponseUpdate, AgentResponse]: ...

    def run(
        self,
        messages: str | Message | Sequence[str | Message] | None = None,
        *,
        stream: bool = False,
        session: AgentSession | None = None,
        **kwargs: Any,
    ) -> Awaitable[AgentResponse] | ResponseStream[AgentResponseUpdate, AgentResponse]:
        options = kwargs.pop("options", None) or {}
        tools = kwargs.pop("tools", None)
        for key in ("compaction_strategy", "tokenizer"):
            if key in kwargs and kwargs[key] is None:
                kwargs.pop(key)
        if set(options) - {"instructions"}:
            raise TypeError("Only instructions are supported in options")
        # AgentExecutor supplies these even when there are no invocation options.
        for key in ("function_invocation_kwargs", "client_kwargs"):
            if key in kwargs and not kwargs[key]:
                kwargs.pop(key)
        if kwargs:
            raise TypeError(f"Unsupported run options: {', '.join(sorted(kwargs))}")
        tools = validate_tools(self.tools if tools is None else tools)
        records = to_records(messages)
        active_session = session if session is not None else self.create_session()
        if stream:
            return ResponseStream(
                self._stream(records, active_session, options, tools),
                finalizer=AgentResponse.from_updates,
            )
        return self._run(records, active_session, options, tools)

    async def _run(
        self, records: list[dict], session: AgentSession, options: dict, tools=None
    ) -> AgentResponse:
        current = invocation.get()
        if current is None or current["session"] is not session:
            raise ValueError("Middleware cannot replace the reconciled session")
        if current["entered"]:
            raise ValueError("Only one Codex execution per middleware invocation is supported")
        current["entered"] = True
        namespace = session.state["maf_codex"]
        binding = namespace.get(self.id)
        context = SessionContext(
            session_id=session.session_id,
            service_session_id=binding["thread_id"] if binding else None,
            input_messages=[
                Message(item["role"], [item["text"]], author_name=item["author"])
                for item in records
            ],
            options=dict(options),
        )
        providers = validate_providers(self.context_providers)
        for provider in providers:
            state = session.state.setdefault(provider.source_id, {})
            if not isinstance(state, dict):
                raise TypeError("Provider state must be a dictionary")
            await provider.before_run(
                agent=self,
                session=session,
                context=context,
                state=state,
            )
        if context.get_middleware():
            raise TypeError("Provider chat/function middleware is not supported")
        selected_tools = validate_tools([*(self.tools if tools is None else tools), *context.tools])
        fingerprint = tool_fingerprint(selected_tools)
        if binding is not None and binding.get("tools_fingerprint", "") != fingerprint:
            raise SessionBindingError("Tool catalog cannot change in a resumed Codex thread")
        usage_owned = binding is None or binding.get("usage_owner") == session.session_id
        prompt = records_to_prompt(to_records(context.get_messages(include_input=True)))
        extra_instructions = [options.get("instructions", ""), *context.instructions]
        if any(not isinstance(item, str) for item in extra_instructions):
            raise TypeError("Instructions must be strings")

        def bind_thread(thread_id: str) -> None:
            namespace[self.id] = bind(
                thread_id, self.config.fingerprint, self.config.history_mode, binding
            )
            namespace[self.id]["tools_fingerprint"] = fingerprint
            namespace[self.id]["usage_owner"] = session.session_id if usage_owned else None

        runtime_kwargs = {
            "thread_id": binding["thread_id"] if binding else None,
            "bind_thread": bind_thread,
        }
        if any(extra_instructions):
            runtime_kwargs["instructions"] = "\n".join(
                item for item in [self.config.instructions, *extra_instructions] if item
            )
        expected_model = binding.get("configured_model") if binding else None
        if selected_tools or self.approval_handler or expected_model:
            bridge = ToolBridge(selected_tools, session, self.approval_handler)
            bridge.expected_model = expected_model
            runtime_kwargs["bridge"] = bridge
        result = await self._runtime.run(prompt, **runtime_kwargs)
        current["completed"] = True
        usage = (
            counter_delta(
                result.usage, binding.get("usage_total") if binding else None, fresh=binding is None
            )
            if usage_owned
            else None
        )
        if isinstance(result.usage, dict) and isinstance(result.usage.get("total"), dict):
            namespace[self.id]["usage_total"] = dict(result.usage["total"])
        namespace[self.id]["configured_model"] = result.metadata.get("configured_model")
        response = AgentResponse(
            messages=[Message("assistant", [result.text], author_name=self.name)],
            agent_id=self.id,
            response_id=result.turn_id,
            usage_details=usage,
            additional_properties={
                "codex_thread_id": result.thread_id,
                "codex": {
                    **result.metadata,
                    "usage_report": result.usage,
                    "usage_scope": "sdk_counter_delta" if usage is not None else "unknown",
                },
            },
        )
        set_response(context, response)
        for provider in reversed(providers):
            await provider.after_run(
                agent=self,
                session=session,
                context=context,
                state=session.state.setdefault(provider.source_id, {}),
            )
        return response

    async def _stream(self, records: list[dict], session: AgentSession, options: dict, tools=None):
        response = await self._run(records, session, options, tools)
        yield AgentResponseUpdate(
            contents=[
                Content.from_text(response.text),
                *(
                    [Content.from_usage(response.usage_details)]
                    if response.usage_details is not None
                    else []
                ),
            ],
            role="assistant",
            author_name=self.name,
            agent_id=self.id,
            response_id=response.response_id,
            additional_properties=response.additional_properties,
        )


class CodexAgent(AgentMiddlewareLayer, _CodexAgentCore):
    """MAF agent-level middleware and text context providers around local Codex.

    Full-history reconciliation happens before hooks. Acknowledged output reflects
    the final visible middleware result, while Codex retains its own raw history.
    """

    def __init__(
        self,
        config: CodexAgentConfig,
        *,
        id: str | None = None,
        name: str = "Codex",
        description: str | None = None,
        runtime: CodexRuntime | None = None,
        middleware: Sequence[Any] | None = None,
        context_providers: Sequence[ContextProvider] | None = None,
        tools: Sequence[FunctionTool] | None = None,
        approval_handler: ApprovalHandler | None = None,
    ):
        providers = validate_providers(context_providers)
        middleware_items = agent_middleware_only(middleware)
        super().__init__(
            config,
            id=id,
            name=name,
            description=description,
            runtime=runtime,
            middleware=middleware_items,
            context_providers=providers,
            tools=tools,
            approval_handler=approval_handler,
        )

    @overload
    def run(
        self,
        messages: str | Message | Sequence[str | Message] | None = None,
        *,
        stream: Literal[False] = False,
        session: AgentSession | None = None,
        middleware: Sequence[Any] | None = None,
        options: dict | None = None,
        tools: Sequence[FunctionTool] | None = None,
        **kwargs: Any,
    ) -> Awaitable[AgentResponse]: ...

    @overload
    def run(
        self,
        messages: str | Message | Sequence[str | Message] | None = None,
        *,
        stream: Literal[True],
        session: AgentSession | None = None,
        middleware: Sequence[Any] | None = None,
        options: dict | None = None,
        tools: Sequence[FunctionTool] | None = None,
        **kwargs: Any,
    ) -> ResponseStream[AgentResponseUpdate, AgentResponse]: ...

    def run(
        self,
        messages: str | Message | Sequence[str | Message] | None = None,
        *,
        stream: bool = False,
        session: AgentSession | None = None,
        middleware: Sequence[Any] | None = None,
        options: dict | None = None,
        tools: Sequence[FunctionTool] | None = None,
        **kwargs: Any,
    ) -> Awaitable[AgentResponse] | ResponseStream[AgentResponseUpdate, AgentResponse]:
        agent_middleware_only(self.middleware)
        middleware = agent_middleware_only(middleware)
        for key in ("function_invocation_kwargs", "client_kwargs"):
            if key in kwargs and not kwargs[key]:
                kwargs.pop(key)
        if kwargs:
            raise TypeError(f"Unsupported run options: {', '.join(sorted(kwargs))}")
        if options is not None and (set(options) - {"instructions"}):
            raise TypeError("Only instructions are supported in options")
        tools = validate_tools(self.tools if tools is None else tools)
        records = to_records(messages)
        active_session = session if session is not None else self.create_session()
        if stream:
            return ResponseStream(
                self._pipeline_stream(records, active_session, middleware, options, tools),
                finalizer=AgentResponse.from_updates,
            )
        return self._pipeline_run(records, active_session, middleware, options, False, tools)

    async def _pipeline_run(self, records, session, middleware, options, stream, tools=None):
        parent = invocation.get()
        if (
            parent
            and parent["agent_id"] == self.id
            and parent["session"].session_id == session.session_id
        ):
            raise ValueError("Recursive invocation of the same agent/session is not supported")
        async with session_lock(self.id, session.session_id):
            namespace = session.state.setdefault("maf_codex", {})
            if not isinstance(namespace, dict):
                raise SessionBindingError("Invalid maf_codex session state")
            binding = namespace.get(self.id)
            if binding is None and session.service_session_id is not None:
                raise SessionBindingError("Use agent.get_session() to bind a provider thread id")
            validate_binding(binding, self.config.fingerprint, self.config.history_mode)
            delta = new_records(records, binding, self.config.history_mode)
            messages = [
                Message(item["role"], [item["text"]], author_name=item["author"]) for item in delta
            ]
            current = {
                "session": session,
                "agent_id": self.id,
                "entered": False,
                "completed": False,
            }
            token = invocation.set(current)
            try:
                result = super().run(
                    messages,
                    session=session,
                    stream=stream,
                    middleware=middleware,
                    options=options,
                    tools=tools,
                )
                response = await result.get_final_response() if stream else await result
            finally:
                invocation.reset(token)
            if response is None:
                response = AgentResponse(messages=[], agent_id=self.id)
            if not current["entered"] and isinstance(response, list) and not response:
                response = AgentResponse(messages=[], agent_id=self.id)
            if not isinstance(response, AgentResponse):
                raise TypeError(
                    "Agent middleware must return AgentResponse or a compatible ResponseStream"
                )
            if response.messages:
                to_records(response.messages)  # Output remains text-only, including overrides.
            if self.config.history_mode == "full" and current["completed"] and response.messages:
                # Cached/denied output must not acknowledge an unexecuted or uncertain turn.
                namespace[self.id]["history"] = [*records, *to_records(response.messages)]
                namespace[self.id]["pending"] = False
            return response

    async def _pipeline_stream(self, records, session, middleware, options, tools=None):
        response = await self._pipeline_run(records, session, middleware, options, True, tools)
        for index, message in enumerate(response.messages):
            yield AgentResponseUpdate(
                contents=[
                    *message.contents,
                    *(
                        [Content.from_usage(response.usage_details)]
                        if index == 0 and response.usage_details is not None
                        else []
                    ),
                ],
                role=message.role,
                author_name=message.author_name,
                message_id=message.message_id or str(uuid4()),
                agent_id=response.agent_id,
                response_id=response.response_id,
                additional_properties=response.additional_properties,
            )
