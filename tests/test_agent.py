import asyncio
import json

import pytest
from agent_framework import (
    AgentResponse,
    AgentSession,
    Content,
    Message,
    SupportsAgentRun,
    WorkflowBuilder,
)

from maf_codex import CodexAgent, CodexAgentConfig, SessionBindingError, TurnFailedError
from maf_codex.runtime import RuntimeResponse


class MemoryRuntime:
    """In-memory conversations, independent of the agent's session implementation."""

    def __init__(self):
        self.conversations = {}
        self.calls = []
        self.fail = False

    async def run(self, prompt, *, thread_id, bind_thread):
        self.calls.append((prompt, thread_id))
        if thread_id is None:
            thread_id = f"thread-{len(self.conversations)}"
            self.conversations[thread_id] = []
        bind_thread(thread_id)
        await asyncio.sleep(0)
        if self.fail:
            raise TurnFailedError("failed")
        history = self.conversations[thread_id]
        text = history[-1] if prompt == "recall" else prompt
        history.append(prompt)
        return RuntimeResponse(text, thread_id, f"turn-{len(history)}")


@pytest.fixture
def setup_agent(tmp_path):
    runtime = MemoryRuntime()
    config = CodexAgentConfig(cwd=tmp_path)
    return CodexAgent(config, id="developer", runtime=runtime), runtime


async def test_response_is_maf_compatible(setup_agent):
    agent, _ = setup_agent
    assert isinstance(agent, SupportsAgentRun)
    response = await agent.run("hello")
    assert isinstance(response, AgentResponse)
    assert response.text == "hello"
    assert response.agent_id == "developer"
    assert response.messages[0].role == "assistant"


async def test_sessions_keep_independent_context(setup_agent):
    agent, runtime = setup_agent
    a, b = agent.create_session(), agent.create_session()
    await agent.run("alpha", session=a)
    await agent.run("beta", session=b)
    assert (await agent.run("recall", session=a)).text == "alpha"
    assert (await agent.run("recall", session=b)).text == "beta"
    assert len(runtime.conversations) == 2


async def test_restored_session_and_new_agent_resume_existing_thread(setup_agent):
    agent, runtime = setup_agent
    session = agent.create_session()
    await agent.run("remember", session=session)
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    replacement = CodexAgent(agent.config, id=agent.id, runtime=runtime)
    assert (await replacement.run("recall", session=restored)).text == "remember"
    assert len(runtime.conversations) == 1


async def test_agents_do_not_share_thread_when_using_same_session(setup_agent):
    agent, runtime = setup_agent
    reviewer = CodexAgent(agent.config, id="reviewer", runtime=runtime)
    session = agent.create_session()
    await agent.run("developer-context", session=session)
    await reviewer.run("review-context", session=session)
    assert (await agent.run("recall", session=session)).text == "developer-context"
    assert (await reviewer.run("recall", session=session)).text == "review-context"


async def test_calls_without_session_are_independent(setup_agent):
    agent, runtime = setup_agent
    await agent.run("one")
    await agent.run("two")
    assert len(runtime.conversations) == 2


async def test_same_session_concurrent_requests_are_serialized(setup_agent):
    agent, runtime = setup_agent
    session = agent.create_session()
    await asyncio.gather(agent.run("one", session=session), agent.run("two", session=session))
    assert len(runtime.conversations) == 1
    assert next(iter(runtime.conversations.values())) == ["one", "two"]


async def test_changed_configuration_fails_before_runtime(setup_agent):
    agent, runtime = setup_agent
    session = agent.create_session()
    await agent.run("hello", session=session)
    changed = CodexAgent(
        CodexAgentConfig(cwd=agent.config.cwd, instructions="new role"),
        id=agent.id,
        runtime=runtime,
    )
    with pytest.raises(SessionBindingError):
        await changed.run("continue", session=session)
    assert len(runtime.calls) == 1


async def test_failed_turn_preserves_binding(setup_agent):
    agent, runtime = setup_agent
    runtime.fail = True
    session = agent.create_session()
    with pytest.raises(TurnFailedError):
        await agent.run("hello", session=session)
    runtime.fail = False
    await agent.run("retry explicitly", session=session)
    assert runtime.calls[-1][1] == "thread-0"


async def test_buffered_stream_final_response(setup_agent):
    agent, _ = setup_agent
    stream = agent.run("hello", stream=True)
    updates = [update async for update in stream]
    assert len(updates) == 1
    assert updates[0].text == "hello"
    assert (await stream.get_final_response()).text == "hello"


@pytest.mark.parametrize("stream", [False, True])
async def test_actual_maf_workflow_invokes_adapter(setup_agent, stream):
    agent, _ = setup_agent
    workflow = WorkflowBuilder(start_executor=agent, output_from=[agent]).build()
    if stream:
        events = [event async for event in workflow.run("hello", stream=True)]
        assert events
    else:
        result = await workflow.run("hello")
        assert any(output.text == "hello" for output in result.get_outputs())


@pytest.mark.parametrize(
    "messages",
    [
        None,
        "",
        Message("system", ["instructions"]),
        Message("user", [Content.from_data(b"abc", "image/png")]),
    ],
)
def test_unsupported_input_is_rejected_before_runtime(setup_agent, messages):
    agent, runtime = setup_agent
    with pytest.raises(ValueError):
        agent.run(messages)
    assert not runtime.calls


def test_unsupported_options_are_explicit(setup_agent):
    agent, _ = setup_agent
    with pytest.raises(TypeError):
        agent.run("hello", tools=[object()])
