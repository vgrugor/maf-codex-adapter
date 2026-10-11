import asyncio
import json

from agent_framework import (
    AgentExecutorResponse,
    AgentSession,
    Executor,
    FunctionInvocationContext,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)

from maf_codex import CodexAgent, CodexAgentConfig
from maf_codex.runtime import RuntimeResponse


class ScriptRuntime:
    def __init__(self, name, replies):
        self.name, self.replies = name, iter(replies)
        self.calls = []

    async def run(self, prompt, *, thread_id, bind_thread):
        self.calls.append((prompt, thread_id))
        thread_id = thread_id or f"thread-{self.name}-{len(self.calls)}"
        bind_thread(thread_id)
        return RuntimeResponse(next(self.replies), thread_id, f"turn-{self.name}-{len(self.calls)}")


async def test_sequential_graph_passes_context_between_agents(tmp_path):
    config = CodexAgentConfig(cwd=tmp_path, history_mode="full")
    runtime_a, runtime_b = ScriptRuntime("a", ["draft"]), ScriptRuntime("b", ["review"])
    a = CodexAgent(config, id="a", name="Writer", runtime=runtime_a)
    b = CodexAgent(config, id="b", name="Reviewer", runtime=runtime_b)
    workflow = WorkflowBuilder(start_executor=a, output_from=[b]).add_edge(a, b).build()
    result = await workflow.run("task")
    assert result.get_outputs()[0].text == "review"
    received = json.loads(runtime_b.calls[0][0].split("\n", 1)[1])
    assert [record["text"] for record in received] == ["task", "draft"]
    assert received[1]["author"] == "Writer"
    assert runtime_a.calls[0][1] is None and runtime_b.calls[0][1] is None


async def test_revisited_agents_in_cycle_do_not_duplicate_full_context(tmp_path):
    config = CodexAgentConfig(cwd=tmp_path, history_mode="full")
    runtime_a = ScriptRuntime("a", ["draft", "fixed"])
    runtime_b = ScriptRuntime("b", ["needs changes", "approved"])
    a = CodexAgent(config, id="a", name="Writer", runtime=runtime_a)
    b = CodexAgent(config, id="b", name="Reviewer", runtime=runtime_b)
    workflow = (
        WorkflowBuilder(start_executor=a, output_from=[b], max_iterations=6)
        .add_edge(a, b)
        .add_edge(b, a, condition=lambda reply: reply.agent_response.text == "needs changes")
        .build()
    )
    result = await workflow.run("task")
    assert result.get_outputs()[-1].text == "approved"
    a_delta = json.loads(runtime_a.calls[1][0].split("\n", 1)[1])
    b_delta = json.loads(runtime_b.calls[1][0].split("\n", 1)[1])
    assert [message["text"] for message in a_delta] == ["needs changes"]
    assert [message["text"] for message in b_delta] == ["fixed"]
    assert runtime_a.calls[1][1] == "thread-a-1"
    assert runtime_b.calls[1][1] == "thread-b-1"


class Collector(Executor):
    def __init__(self):
        super().__init__(id="collector")

    @handler
    async def gather(
        self, replies: list[AgentExecutorResponse], ctx: WorkflowContext[None, list[str]]
    ):
        await ctx.yield_output(sorted(reply.agent_response.text for reply in replies))


async def test_fan_out_agents_overlap_and_fan_in_collects_both(tmp_path):
    entered = set()
    both_entered = asyncio.Event()

    class BarrierRuntime(ScriptRuntime):
        async def run(self, prompt, *, thread_id, bind_thread):
            entered.add(self.name)
            if len(entered) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=2)
            return await super().run(prompt, thread_id=thread_id, bind_thread=bind_thread)

    config = CodexAgentConfig(cwd=tmp_path)
    router = CodexAgent(
        config, id="router", name="Router", runtime=ScriptRuntime("router", ["route"])
    )
    left = CodexAgent(
        config, id="left", name="Left", runtime=BarrierRuntime("left", ["left-result"])
    )
    right = CodexAgent(
        config, id="right", name="Right", runtime=BarrierRuntime("right", ["right-result"])
    )
    collector = Collector()
    workflow = (
        WorkflowBuilder(start_executor=router, output_from=[collector])
        .add_fan_out_edges(router, [left, right])
        .add_fan_in_edges([left, right], collector)
        .build()
    )
    result = await workflow.run("task")
    assert result.get_outputs() == [["left-result", "right-result"]]
    assert entered == {"left", "right"}


async def test_agent_as_tool_uses_private_provider_sessions(tmp_path):
    runtime = ScriptRuntime("child", ["one", "two"])
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), id="child", name="Child", runtime=runtime)
    tool = agent.as_tool()
    assert (await tool.invoke(arguments={"task": "first"}))[0].text == "one"
    assert (await tool.invoke(arguments={"task": "second"}))[0].text == "two"
    assert all(thread_id is None for _, thread_id in runtime.calls)


async def test_propagated_tool_session_does_not_inherit_codex_bindings(tmp_path):
    runtime = ScriptRuntime("child", ["result"])
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), id="child", name="Child", runtime=runtime)
    parent = AgentSession()
    parent.state["maf_codex"] = {"child": {"thread_id": "parent-thread"}}
    tool = agent.as_tool(propagate_session=True)
    context = FunctionInvocationContext(
        function=tool,
        arguments={"task": "task"},
        session=parent,
        parent_service_session_state_keys={"maf_codex"},
    )
    assert (await tool.invoke(arguments={"task": "task"}, context=context))[0].text == "result"
    assert runtime.calls[0][1] is None
    assert parent.state["maf_codex"] == {"child": {"thread_id": "parent-thread"}}
