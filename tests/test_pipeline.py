import json

import pytest
from agent_framework import (
    AgentContext,
    AgentMiddleware,
    AgentResponse,
    AgentSession,
    ChatMiddleware,
    ContextProvider,
    HistoryProvider,
    Message,
    agent_middleware,
)

from maf_codex import CodexAgent, CodexAgentConfig, HistoryMismatchError
from maf_codex.runtime import RuntimeResponse


class Runtime:
    def __init__(self, events=None):
        self.calls = []
        self.events = events if events is not None else []

    async def run(self, prompt, *, thread_id, bind_thread, instructions=None):
        self.events.append("runtime")
        self.calls.append((prompt, thread_id, instructions))
        bind_thread(thread_id or "thread")
        return RuntimeResponse("raw", thread_id or "thread", "turn")


class TraceMiddleware(AgentMiddleware):
    def __init__(self, name, events):
        self.name, self.events = name, events

    async def process(self, context, call_next):
        assert isinstance(context.agent, CodexAgent)
        self.events.append(f"{self.name}:before")
        await call_next()
        self.events.append(f"{self.name}:after")


class Provider(ContextProvider):
    def __init__(self, name, events):
        super().__init__(name)
        self.events = events

    async def before_run(self, *, agent, session, context, state):
        self.events.append(f"{self.source_id}:before")
        state["count"] = state.get("count", 0) + 1
        context.extend_messages(self.source_id, [Message("user", [f"context-{self.source_id}"])])
        context.extend_instructions(self.source_id, f"instruction-{self.source_id}")

    async def after_run(self, *, agent, session, context, state):
        self.events.append(f"{self.source_id}:after")
        state["last_response"] = context.response.text


@pytest.mark.parametrize("stream", [False, True])
async def test_standard_maf_pipeline_order_and_instruction_mapping(tmp_path, stream):
    events, runtime = [], Runtime()
    runtime.events = events
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path, instructions="base"),
        id="pipeline",
        runtime=runtime,
        middleware=[TraceMiddleware("outer", events)],
        context_providers=[Provider("p1", events), Provider("p2", events)],
    )
    session = agent.create_session()
    result = agent.run(
        "task",
        session=session,
        stream=stream,
        middleware=[TraceMiddleware("inner", events)],
        options={"instructions": "run"},
    )
    response = await result.get_final_response() if stream else await result
    assert response.text == "raw"
    ordinary = [
        "outer:before",
        "inner:before",
        "p1:before",
        "p2:before",
        "runtime",
        "p2:after",
        "p1:after",
        "inner:after",
        "outer:after",
    ]
    assert events == (
        ordinary
        if not stream
        else [
            "outer:before",
            "inner:before",
            "inner:after",
            "outer:after",
            "p1:before",
            "p2:before",
            "runtime",
            "p2:after",
            "p1:after",
        ]
    )
    assert runtime.calls[0][2] == "base\nrun\ninstruction-p1\ninstruction-p2"
    records = json.loads(runtime.calls[0][0].split("\n", 1)[1])
    assert [record["text"] for record in records] == ["context-p1", "context-p2", "task"]
    assert session.state["p1"] == {"count": 1, "last_response": "raw"}


@pytest.mark.parametrize("stream", [False, True])
async def test_decorated_middleware_changes_only_new_input(tmp_path, stream):
    @agent_middleware
    async def change(context: AgentContext, call_next):
        context.messages = [Message("user", ["modified"])]
        await call_next()

    runtime = Runtime()
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), middleware=[change], runtime=runtime)
    original = Message("user", ["original"])
    result = agent.run(original, stream=stream)
    await result.get_final_response() if stream else await result
    assert runtime.calls[0][0] == "modified"
    assert original.text == "original"


@pytest.mark.parametrize("stream", [False, True])
async def test_early_termination_skips_providers_and_runtime(tmp_path, stream):
    @agent_middleware
    async def stop(context: AgentContext, call_next):
        return  # No call_next: framework returns an empty result.

    events, runtime = [], Runtime()
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path),
        middleware=[stop],
        context_providers=[Provider("p", events)],
        runtime=runtime,
    )
    result = agent.run("task", stream=stream)
    response = await result.get_final_response() if stream else await result
    assert response.text == ""
    assert runtime.calls == [] and events == []


class VisibleResult(AgentMiddleware):
    async def process(self, context, call_next):
        await call_next()
        if context.stream:

            def transform(response):
                response.messages = [Message("assistant", ["visible"], author_name="Codex")]
                return response

            context.stream_result_transforms.append(transform)
        else:
            context.result.messages = [Message("assistant", ["visible"], author_name="Codex")]


@pytest.mark.parametrize("stream", [False, True])
async def test_full_history_tracks_visible_output_but_excludes_provider_context(tmp_path, stream):
    runtime = Runtime()
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path, history_mode="full"),
        id="full",
        runtime=runtime,
        middleware=[VisibleResult()],
        context_providers=[Provider("p", [])],
    )
    session = agent.create_session()
    user = Message("user", ["first"])
    result = agent.run(user, session=session, stream=stream)
    response = await result.get_final_response() if stream else await result
    assert response.text == "visible"
    result = agent.run(
        [user, *response.messages, Message("user", ["second"])], session=session, stream=stream
    )
    await result.get_final_response() if stream else await result
    sent = json.loads(runtime.calls[1][0].split("\n", 1)[1])
    assert [record["text"] for record in sent] == ["context-p", "second"]
    assert session.state["p"]["count"] == 2


async def test_provider_state_survives_session_restoration(tmp_path):
    config = CodexAgentConfig(cwd=tmp_path)
    agent = CodexAgent(config, id="a", runtime=Runtime(), context_providers=[Provider("p", [])])
    session = agent.create_session()
    await agent.run("first", session=session)
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    replacement = CodexAgent(
        config, id="a", runtime=Runtime(), context_providers=[Provider("p", [])]
    )
    await replacement.run("second", session=restored)
    assert restored.state["p"]["count"] == 2


async def test_provider_failure_skips_runtime_and_after_hooks(tmp_path):
    class Failure(Provider):
        async def before_run(self, **kwargs):
            raise LookupError("provider failed")

    events, runtime = [], Runtime()
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path), runtime=runtime, context_providers=[Failure("p", events)]
    )
    with pytest.raises(LookupError):
        await agent.run("task")
    assert runtime.calls == [] and events == []


async def test_middleware_can_observe_runtime_errors(tmp_path):
    @agent_middleware
    async def observer(context: AgentContext, call_next):
        with pytest.raises(LookupError):
            await call_next()
        context.result = AgentResponse(messages=[Message("assistant", ["handled"])])

    class Failure(Runtime):
        async def run(self, *args, **kwargs):
            raise LookupError("runtime failed")

    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), middleware=[observer], runtime=Failure())
    assert (await agent.run("task")).text == "handled"


async def test_unsupported_provider_tools_are_rejected_before_runtime(tmp_path):
    class Tools(ContextProvider):
        async def before_run(self, **kwargs):
            kwargs["context"].tools.append(object())

    runtime = Runtime()
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path), runtime=runtime, context_providers=[Tools("tools")]
    )
    with pytest.raises(TypeError):
        await agent.run("task")
    assert runtime.calls == []


@pytest.mark.parametrize(
    "providers", [[ContextProvider("maf_codex")], [ContextProvider("p"), ContextProvider("p")]]
)
def test_provider_ids_do_not_collide_with_state(tmp_path, providers):
    with pytest.raises(ValueError):
        CodexAgent(CodexAgentConfig(cwd=tmp_path), context_providers=providers)


def test_history_provider_is_not_replayed_into_codex(tmp_path):
    with pytest.raises(TypeError):
        CodexAgent(CodexAgentConfig(cwd=tmp_path), context_providers=[HistoryProvider("history")])


def test_chat_middleware_is_explicitly_rejected(tmp_path):
    class Chat(ChatMiddleware):
        async def process(self, context, call_next):
            await call_next()

    with pytest.raises(TypeError):
        CodexAgent(CodexAgentConfig(cwd=tmp_path), middleware=[Chat()])


async def test_session_replacement_is_rejected(tmp_path):
    @agent_middleware
    async def replace(context: AgentContext, call_next):
        context.session = AgentSession()
        await call_next()

    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=Runtime(), middleware=[replace])
    with pytest.raises(ValueError, match="replace"):
        await agent.run("task")


async def test_repeated_call_next_is_rejected(tmp_path):
    @agent_middleware
    async def twice(context: AgentContext, call_next):
        await call_next()
        await call_next()

    runtime = Runtime()
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=runtime, middleware=[twice])
    with pytest.raises(ValueError, match="one Codex"):
        await agent.run("task")
    assert len(runtime.calls) == 1


async def test_failed_full_turn_is_not_acknowledged_by_error_fallback(tmp_path):
    class Failure(Runtime):
        async def run(self, prompt, *, bind_thread, **kwargs):
            bind_thread("thread")
            raise LookupError("uncertain")

    @agent_middleware
    async def handle(context: AgentContext, call_next):
        try:
            await call_next()
        except LookupError:
            context.result = AgentResponse(messages=[Message("assistant", ["fallback"])])

    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path, history_mode="full"), runtime=Failure(), middleware=[handle]
    )
    session = agent.create_session()
    response = await agent.run("task", session=session)
    with pytest.raises(HistoryMismatchError):
        await agent.run(
            [Message("user", ["task"]), *response.messages, Message("user", ["next"])],
            session=session,
        )
