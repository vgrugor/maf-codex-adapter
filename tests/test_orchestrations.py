"""Real orchestration builders, offline runtime: no Codex process or plan usage."""

import asyncio
import json

import pytest
from agent_framework.orchestrations import (
    ConcurrentBuilder,
    GroupChatBuilder,
    HandoffBuilder,
    SequentialBuilder,
)

from maf_codex import CodexAgent, CodexAgentConfig, HistoryMismatchError
from maf_codex.runtime import RuntimeResponse


class ScriptRuntime:
    def __init__(self, name, replies, barrier=None):
        self.name = name
        self.replies = iter(replies)
        self.calls = []
        self.barrier = barrier

    async def run(self, prompt, *, thread_id, bind_thread):
        self.calls.append((prompt, thread_id))
        if self.barrier:
            await asyncio.wait_for(self.barrier.wait(), timeout=2)
        thread_id = thread_id or f"thread-{self.name}"
        bind_thread(thread_id)
        return RuntimeResponse(next(self.replies), thread_id, f"turn-{len(self.calls)}")


def make_agent(tmp_path, name, replies, barrier=None, history_mode="full"):
    runtime = ScriptRuntime(name, replies, barrier)
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path, history_mode=history_mode),
        id=name,
        name=name,
        runtime=runtime,
    )
    return agent, runtime


def records(prompt):
    if prompt.startswith("Process the following incoming conversation messages."):
        return json.loads(prompt.split("\n", 1)[1])
    return [{"role": "user", "author": None, "text": prompt}]


@pytest.mark.parametrize("stream", [False, True])
async def test_sequential_builder_context_and_output(tmp_path, stream):
    writer, writer_runtime = make_agent(tmp_path, "Writer", ["draft"])
    reviewer, reviewer_runtime = make_agent(tmp_path, "Reviewer", ["review"])
    workflow = SequentialBuilder(participants=[writer, reviewer]).build()
    if stream:
        outputs = [
            event.data
            async for event in workflow.run("task", stream=True)
            if event.type == "output"
        ]
    else:
        outputs = (await workflow.run("task")).get_outputs()
    assert outputs[-1].text == "review"
    assert [r["text"] for r in records(reviewer_runtime.calls[0][0])] == ["task", "draft"]
    assert records(reviewer_runtime.calls[0][0])[1]["author"] == "Writer"
    assert writer_runtime.calls[0][1] is None
    assert reviewer_runtime.calls[0][1] is None


async def test_concurrent_builder_runs_branches_together_and_aggregates(tmp_path):
    barrier = asyncio.Barrier(2)
    left, left_runtime = make_agent(tmp_path, "Left", ["left"], barrier)
    right, right_runtime = make_agent(tmp_path, "Right", ["right"], barrier)
    workflow = ConcurrentBuilder(participants=[left, right]).build()
    outputs = (await workflow.run("task")).get_outputs()
    assert sorted(m.text for m in outputs[-1].messages) == ["left", "right"]
    assert left_runtime.calls == [("task", None)]
    assert right_runtime.calls == [("task", None)]


@pytest.mark.parametrize("stream", [False, True])
async def test_group_chat_revisits_agent_without_duplicate_context(tmp_path, stream):
    writer, writer_runtime = make_agent(
        tmp_path, "Writer", ["draft", "fixed"], history_mode="incremental"
    )
    reviewer, reviewer_runtime = make_agent(
        tmp_path, "Reviewer", ["needs changes"], history_mode="incremental"
    )
    states = []

    def select(state):
        states.append([m.text for m in state.conversation])
        return ["Writer", "Reviewer", "Writer"][state.current_round]

    workflow = GroupChatBuilder(
        participants=[writer, reviewer], selection_func=select, max_rounds=3
    ).build()
    if stream:
        outputs = [
            event.data
            async for event in workflow.run("task", stream=True)
            if event.type == "output"
        ]
    else:
        outputs = (await workflow.run("task")).get_outputs()
    assert outputs
    assert states == [["task"], ["task", "draft"], ["task", "draft", "needs changes"]]
    assert [r["text"] for r in records(reviewer_runtime.calls[0][0])] == ["task", "draft"]
    assert [r["text"] for r in records(writer_runtime.calls[1][0])] == ["needs changes"]
    assert writer_runtime.calls[1][1] == "thread-Writer"
    assert reviewer_runtime.calls[0][1] is None


async def test_group_chat_default_executor_requires_incremental_history(tmp_path):
    writer, runtime = make_agent(tmp_path, "Writer", ["draft", "unused"])
    reviewer, _ = make_agent(tmp_path, "Reviewer", ["review"])
    workflow = GroupChatBuilder(
        participants=[writer, reviewer],
        selection_func=lambda state: ["Writer", "Reviewer", "Writer"][state.current_round],
        max_rounds=3,
    ).build()
    with pytest.raises(HistoryMismatchError, match="exact acknowledged"):
        await workflow.run("task")
    assert len(runtime.calls) == 1


def test_handoff_builder_rejects_custom_agent_before_runtime(tmp_path):
    triage, runtime = make_agent(tmp_path, "Triage", ["unused"])
    with pytest.raises(TypeError, match="Participants must be Agent instances"):
        HandoffBuilder(participants=[triage])
    assert runtime.calls == []


def test_group_chat_native_agent_manager_rejects_custom_agent(tmp_path):
    participant, runtime = make_agent(tmp_path, "Participant", ["unused"])
    manager, manager_runtime = make_agent(tmp_path, "Manager", ["unused"])
    with pytest.raises(TypeError, match="not callable"):
        GroupChatBuilder(participants=[participant], orchestrator_agent=manager).build()
    assert runtime.calls == manager_runtime.calls == []
