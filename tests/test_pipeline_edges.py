import asyncio

import pytest
from agent_framework import (
    AgentContext,
    AgentResponse,
    ChatMiddleware,
    Content,
    ContextProvider,
    Message,
    agent_middleware,
)

from maf_codex import CodexAgent, CodexAgentConfig, HistoryMismatchError
from maf_codex.runtime import RuntimeResponse


class Runtime:
    def __init__(self):
        self.calls = []

    async def run(self, prompt, *, thread_id, bind_thread, instructions=None):
        self.calls.append(prompt)
        bind_thread(thread_id or "thread")
        return RuntimeResponse("raw", thread_id or "thread", "turn")


async def test_full_cached_response_does_not_acknowledge_unsubmitted_input(tmp_path):
    @agent_middleware
    async def cache(context: AgentContext, call_next):
        if len(context.messages) == 1 and context.messages[0].text == "cached-task":
            context.result = AgentResponse(
                messages=[Message("assistant", ["cached"], author_name="Codex")]
            )
        else:
            await call_next()

    runtime = Runtime()
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path, history_mode="full"), runtime=runtime, middleware=[cache]
    )
    session = agent.create_session()
    first = Message("user", ["first"])
    raw = await agent.run(first, session=session)
    cached_input = Message("user", ["cached-task"])
    cached = await agent.run([first, *raw.messages, cached_input], session=session)
    await agent.run(
        [first, *raw.messages, cached_input, *cached.messages, Message("user", ["next"])],
        session=session,
    )
    assert len(runtime.calls) == 2
    assert "cached-task" in runtime.calls[1] and "cached" in runtime.calls[1]


async def test_provider_after_error_leaves_full_state_uncertain(tmp_path):
    class AfterFailure(ContextProvider):
        async def after_run(self, **kwargs):
            raise LookupError("persistence failed")

    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path, history_mode="full"),
        runtime=Runtime(),
        context_providers=[AfterFailure("p")],
    )
    session = agent.create_session()
    with pytest.raises(LookupError):
        await agent.run("task", session=session)
    with pytest.raises(HistoryMismatchError):
        await agent.run("task", session=session)


async def test_stream_result_hook_runs_after_provider_completion(tmp_path):
    events = []

    class Observe(ContextProvider):
        async def after_run(self, **kwargs):
            events.append("provider-after")

    @agent_middleware
    async def observe(context: AgentContext, call_next):
        await call_next()
        events.append("middleware-setup")

        def completed(response):
            events.append("middleware-completed")
            return response

        context.stream_result_transforms.append(completed)

    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path),
        runtime=Runtime(),
        middleware=[observe],
        context_providers=[Observe("p")],
    )
    await agent.run("task", stream=True).get_final_response()
    assert events == ["middleware-setup", "provider-after", "middleware-completed"]


async def test_stream_keeps_multiple_middleware_output_messages(tmp_path):
    @agent_middleware
    async def split(context: AgentContext, call_next):
        await call_next()

        def completed(response):
            response.messages = [
                Message("assistant", ["one"], author_name="A"),
                Message("assistant", ["two"], author_name="B"),
            ]
            return response

        context.stream_result_transforms.append(completed)

    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=Runtime(), middleware=[split])
    response = await agent.run("task", stream=True).get_final_response()
    assert [(message.author_name, message.text) for message in response.messages] == [
        ("A", "one"),
        ("B", "two"),
    ]


async def test_cancellation_reaches_middleware_cleanup(tmp_path):
    started, cleaned = asyncio.Event(), asyncio.Event()

    class Waiting(Runtime):
        async def run(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()

    @agent_middleware
    async def cleanup(context: AgentContext, call_next):
        try:
            await call_next()
        finally:
            cleaned.set()

    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=Waiting(), middleware=[cleanup])
    task = asyncio.create_task(agent.run("task"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


async def test_stream_update_transform_is_applied(tmp_path):
    @agent_middleware
    async def change(context: AgentContext, call_next):
        await call_next()

        def transform(update):
            update.contents = [Content.from_text("changed")]
            return update

        context.stream_update_transforms.append(transform)

    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=Runtime(), middleware=[change])
    assert (await agent.run("task", stream=True).get_final_response()).text == "changed"


def test_run_chat_middleware_is_rejected_before_early_termination(tmp_path):
    class Chat(ChatMiddleware):
        async def process(self, context, call_next):
            await call_next()

    @agent_middleware
    async def stop(context: AgentContext, call_next):
        return

    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=Runtime(), middleware=[stop])
    with pytest.raises(TypeError):
        agent.run("task", middleware=[Chat()])


async def test_invalid_provider_state_is_rejected_before_runtime(tmp_path):
    runtime = Runtime()
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path), runtime=runtime, context_providers=[ContextProvider("p")]
    )
    session = agent.create_session()
    session.state["p"] = "invalid"
    with pytest.raises(TypeError):
        await agent.run("task", session=session)
    assert not runtime.calls


async def test_recursive_same_session_call_fails_without_deadlocking(tmp_path):
    @agent_middleware
    async def recurse(context: AgentContext, call_next):
        await context.agent.run("nested", session=context.session)

    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=Runtime(), middleware=[recurse])
    with pytest.raises(ValueError, match="Recursive"):
        await asyncio.wait_for(agent.run("task"), timeout=1)
