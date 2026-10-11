"""A minimal MAF agent backed by local Codex SDK."""

from collections.abc import Awaitable, Sequence
from typing import Any, Literal, overload

from agent_framework import (
    AgentResponse,
    AgentResponseUpdate,
    AgentSession,
    BaseAgent,
    Content,
    Message,
    ResponseStream,
)

from .config import CodexAgentConfig
from .errors import SessionBindingError
from .mapping import records_to_prompt, to_records
from .runtime import CodexRuntime
from .sessions import bind, new_records, session_lock, validate_binding


class CodexAgent(BaseAgent):
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
    ):
        super().__init__(id=id, name=name, description=description)
        self.config = config
        self._runtime = runtime or CodexRuntime(config)

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
        # AgentExecutor supplies these even when there are no invocation options.
        for key in ("function_invocation_kwargs", "client_kwargs"):
            if key in kwargs and not kwargs[key]:
                kwargs.pop(key)
        if kwargs:
            raise TypeError(f"Unsupported run options: {', '.join(sorted(kwargs))}")
        records = to_records(messages)
        active_session = session if session is not None else self.create_session()
        if stream:
            return ResponseStream(
                self._stream(records, active_session), finalizer=AgentResponse.from_updates
            )
        return self._run(records, active_session)

    async def _run(self, records: list[dict], session: AgentSession) -> AgentResponse:
        async with session_lock(self.id, session.session_id):
            namespace = session.state.setdefault("maf_codex", {})
            if not isinstance(namespace, dict):
                raise SessionBindingError("Invalid maf_codex session state")
            binding = namespace.get(self.id)
            if binding is None and session.service_session_id is not None:
                raise SessionBindingError("Use agent.get_session() to bind a provider thread id")
            validate_binding(binding, self.config.fingerprint, self.config.history_mode)
            delta = new_records(records, binding, self.config.history_mode)
            prompt = records_to_prompt(delta)

            def bind_thread(thread_id: str) -> None:
                namespace[self.id] = bind(
                    thread_id, self.config.fingerprint, self.config.history_mode, binding
                )

            result = await self._runtime.run(
                prompt,
                thread_id=binding["thread_id"] if binding else None,
                bind_thread=bind_thread,
            )
            if self.config.history_mode == "full":
                namespace[self.id]["history"] = [
                    *records,
                    {"role": "assistant", "author": self.name, "text": result.text},
                ]
                namespace[self.id]["pending"] = False
            return AgentResponse(
                messages=[Message("assistant", [result.text], author_name=self.name)],
                agent_id=self.id,
                response_id=result.turn_id,
                additional_properties={"codex_thread_id": result.thread_id},
            )

    async def _stream(self, records: list[dict], session: AgentSession):
        response = await self._run(records, session)
        yield AgentResponseUpdate(
            contents=[Content.from_text(response.text)],
            role="assistant",
            author_name=self.name,
            agent_id=self.id,
            response_id=response.response_id,
            additional_properties=response.additional_properties,
        )
