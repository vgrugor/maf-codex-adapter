import asyncio
import json

import pytest
from agent_framework import AgentSession, Message

from maf_codex import (
    CodexAgent,
    CodexAgentConfig,
    HistoryMismatchError,
    SessionBindingError,
    TurnFailedError,
)
from maf_codex.runtime import RuntimeResponse


class RecordingRuntime:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def run(self, prompt, *, thread_id, bind_thread):
        self.calls.append((prompt, thread_id))
        bind_thread(thread_id or "thread-history")
        if self.fail:
            raise TurnFailedError("failed after binding")
        return RuntimeResponse("answer", thread_id or "thread-history", "turn-history")


@pytest.fixture
def full_agent(tmp_path):
    runtime = RecordingRuntime()
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path, history_mode="full"), id="full", runtime=runtime
    )
    return agent, runtime


@pytest.mark.parametrize("stream", [False, True])
async def test_full_history_sends_only_new_suffix(full_agent, stream):
    agent, runtime = full_agent
    session = agent.create_session()
    first = [Message("user", ["first"])]
    response = await agent.run(first, session=session)
    full = [*first, *response.messages, Message("user", ["second"])]
    result = agent.run(full, session=session, stream=stream)
    await result.get_final_response() if stream else await result
    assert runtime.calls == [("first", None), ("second", "thread-history")]


async def test_full_history_survives_serialization(full_agent):
    agent, runtime = full_agent
    session = agent.create_session()
    first = Message("user", ["first"])
    response = await agent.run(first, session=session)
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    await agent.run([first, *response.messages, Message("user", ["second"])], session=restored)
    assert runtime.calls[-1] == ("second", "thread-history")


@pytest.mark.parametrize("incoming", ["new delta only", [Message("user", ["changed past"])]])
async def test_divergent_full_history_is_rejected_without_runtime(full_agent, incoming):
    agent, runtime = full_agent
    session = agent.create_session()
    await agent.run("first", session=session)
    with pytest.raises(HistoryMismatchError):
        await agent.run(incoming, session=session)
    assert len(runtime.calls) == 1


async def test_same_full_snapshot_is_not_reexecuted(full_agent):
    agent, runtime = full_agent
    session = agent.create_session()
    first = Message("user", ["first"])
    response = await agent.run(first, session=session)
    with pytest.raises(HistoryMismatchError):
        await agent.run([first, *response.messages], session=session)
    assert len(runtime.calls) == 1


async def test_repeated_text_in_incremental_mode_is_a_new_request(tmp_path):
    runtime = RecordingRuntime()
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=runtime)
    session = agent.create_session()
    await agent.run("same task", session=session)
    await agent.run("same task", session=session)
    assert len(runtime.calls) == 2


async def test_failed_full_turn_blocks_ambiguous_continuation(full_agent):
    agent, runtime = full_agent
    session = agent.create_session()
    runtime.fail = True
    with pytest.raises(TurnFailedError):
        await agent.run("first", session=session)
    runtime.fail = False
    with pytest.raises(HistoryMismatchError):
        await agent.run("first", session=session)
    assert len(runtime.calls) == 1


async def test_get_session_resumes_provider_id(tmp_path):
    runtime = RecordingRuntime()
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), id="resume", runtime=runtime)
    session = agent.get_session("existing-thread")
    await agent.run("continue", session=session)
    assert runtime.calls == [("continue", "existing-thread")]


@pytest.mark.parametrize("thread_id", [None, "", "  ", {"id": "thread"}])
def test_get_session_rejects_invalid_id(tmp_path, thread_id):
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path))
    with pytest.raises(SessionBindingError):
        agent.get_session(thread_id)


def test_get_session_full_mode_requires_snapshot(full_agent):
    agent, _ = full_agent
    with pytest.raises(SessionBindingError):
        agent.get_session("thread-without-history")


async def test_unknown_state_version_is_rejected(tmp_path):
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=RecordingRuntime())
    session = agent.get_session("thread")
    session.state["maf_codex"][agent.id]["version"] = 99
    with pytest.raises(SessionBindingError):
        await agent.run("continue", session=session)


async def test_legacy_incremental_binding_migrates(tmp_path):
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=RecordingRuntime())
    session = agent.create_session()
    session.state["maf_codex"] = {
        agent.id: {"thread_id": "old-thread", "fingerprint": agent.config.fingerprint}
    }
    await agent.run("continue", session=session)
    assert session.state["maf_codex"][agent.id]["version"] == 1


async def test_locks_are_shared_by_recreated_instances(tmp_path):
    class SlowRuntime(RecordingRuntime):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run(self, prompt, *, thread_id, bind_thread):
            self.started.set()
            await self.release.wait()
            return await super().run(prompt, thread_id=thread_id, bind_thread=bind_thread)

    runtime = SlowRuntime()
    config = CodexAgentConfig(cwd=tmp_path)
    one, two = (
        CodexAgent(config, id="same", runtime=runtime),
        CodexAgent(config, id="same", runtime=runtime),
    )
    session = one.create_session()
    task = asyncio.create_task(one.run("one", session=session))
    await runtime.started.wait()
    second = asyncio.create_task(two.run("two", session=session))
    await asyncio.sleep(0)
    runtime.release.set()
    await asyncio.gather(task, second)
    assert runtime.calls == [("one", None), ("two", "thread-history")]


def test_history_mode_is_validated(tmp_path):
    with pytest.raises(ValueError):
        CodexAgentConfig(cwd=tmp_path, history_mode="guess")
