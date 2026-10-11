from types import SimpleNamespace as NS

import pytest
from agent_framework import AgentSession, ContextProvider, FunctionTool
from openai_codex.types import TurnStatus

from maf_codex import CodexAgent, CodexAgentConfig, SessionBindingError
from maf_codex.runtime import CodexRuntime, RuntimeResponse


class SDK:
    def __init__(self, config):
        self.bridge = None
        self.closed = False

    def install_bridge(self, bridge):
        self.bridge = bridge

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def account(self):
        return NS(account=NS(root=NS(type="chatgpt")))

    async def thread_start(self, **kwargs):
        return NS(id="thread", turn=self.turn)

    async def thread_resume(self, thread_id, **kwargs):
        return NS(id=thread_id, turn=self.turn)

    async def turn(self, prompt):
        return NS(id="turn", run=self.run, interrupt=self.interrupt)

    async def interrupt(self):
        pass

    async def run(self):
        call = {
            "threadId": "thread",
            "turnId": "turn",
            "callId": "call",
            "tool": "maf_echo",
            "arguments": {"text": "hello"},
        }
        result = await self.bridge.handle("item/tool/call", call)
        return NS(
            status=TurnStatus.completed, final_response=result["contentItems"][0]["text"], id="turn"
        )


@pytest.mark.parametrize("stream", [False, True])
async def test_agent_sdk_bridge_runs_tool_in_current_session(tmp_path, monkeypatch, stream):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    config = CodexAgentConfig(cwd=tmp_path)
    client = SDK(None)
    runtime = CodexRuntime(config, client_factory=lambda _: client)
    calls = []

    def echo(text: str):
        calls.append(text)
        return text

    agent = CodexAgent(
        config, id="agent", runtime=runtime, tools=[FunctionTool(name="echo", func=echo)]
    )
    session = agent.create_session()
    pending = agent.run("task", session=session, stream=stream)
    response = await pending.get_final_response() if stream else await pending
    assert response.text == "hello" and calls == ["hello"]
    assert client.bridge.session is session
    assert client.closed and client.bridge.closed
    assert session.state["maf_codex"]["agent"]["tools_fingerprint"]


async def test_resumed_tool_catalog_cannot_change(tmp_path):
    class Runtime:
        async def run(self, prompt, *, bind_thread, **kwargs):
            bind_thread("thread")
            return RuntimeResponse("ok", "thread", "turn")

    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path),
        runtime=Runtime(),
        tools=[FunctionTool(name="one", func=lambda: "ok")],
    )
    session = agent.create_session()
    await agent.run("first", session=session)
    with pytest.raises(SessionBindingError):
        await agent.run(
            "next", session=session, tools=[FunctionTool(name="two", func=lambda: "ok")]
        )


def test_tools_do_not_resume_unbound_external_thread(tmp_path):
    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path), tools=[FunctionTool(name="echo", func=lambda: "ok")]
    )
    with pytest.raises(SessionBindingError):
        agent.get_session("external-thread")


async def test_provider_can_contribute_supported_tool(tmp_path):
    class Provider(ContextProvider):
        async def before_run(self, **kwargs):
            kwargs["context"].tools.append(FunctionTool(name="echo", func=lambda: "ok"))

    class Runtime:
        async def run(self, prompt, *, bind_thread, bridge, **kwargs):
            assert "maf_echo" in bridge.tools
            bind_thread("thread")
            return RuntimeResponse("ok", "thread", "turn")

    agent = CodexAgent(
        CodexAgentConfig(cwd=tmp_path), runtime=Runtime(), context_providers=[Provider("p")]
    )
    assert (await agent.run("task")).text == "ok"


async def test_child_agent_as_dynamic_tool_has_private_provider_context(tmp_path):
    from maf_codex.tools import ToolBridge

    class Runtime:
        async def run(self, prompt, *, thread_id, bind_thread):
            assert thread_id is None
            bind_thread("child-thread")
            return RuntimeResponse("child-result", "child-thread", "child-turn")

    child = CodexAgent(CodexAgentConfig(cwd=tmp_path), id="child", name="Child", runtime=Runtime())
    parent = AgentSession()
    parent.state["maf_codex"] = {"parent": {"thread_id": "parent-thread"}}
    bridge = ToolBridge([child.as_tool()], parent)
    bridge.start_thread("parent-thread")
    bridge.start_turn("parent-turn")
    result = await bridge.handle(
        "item/tool/call",
        {
            "threadId": "parent-thread",
            "turnId": "parent-turn",
            "callId": "child-call",
            "tool": "maf_Child",
            "arguments": {"task": "delegate"},
        },
    )
    assert result["success"] and result["contentItems"][0]["text"] == "child-result"
    assert "child" not in parent.state["maf_codex"]
