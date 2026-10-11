import pytest
from agent_framework import AgentSession

from maf_codex import CodexAgent, CodexAgentConfig, HistoryMismatchError, SessionBindingError
from maf_codex.runtime import RuntimeResponse


class Runtime:
    async def run(self, prompt, *, thread_id, bind_thread):
        bind_thread(thread_id or "thread")
        return RuntimeResponse("response", "thread", "turn")


async def test_unbound_service_session_id_is_not_silently_ignored(tmp_path):
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=Runtime())
    with pytest.raises(SessionBindingError):
        await agent.run("continue", session=AgentSession(service_session_id="thread"))


@pytest.mark.parametrize("version", [[], True, None])
async def test_malformed_version_raises_session_error(tmp_path, version):
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=Runtime())
    session = agent.get_session("thread")
    session.state["maf_codex"][agent.id]["version"] = version
    with pytest.raises(SessionBindingError):
        await agent.run("continue", session=session)


async def test_history_policy_change_does_not_silently_reuse_session(tmp_path):
    incremental = CodexAgent(CodexAgentConfig(cwd=tmp_path), id="agent", runtime=Runtime())
    full = CodexAgent(
        CodexAgentConfig(cwd=tmp_path, history_mode="full"), id="agent", runtime=Runtime()
    )
    session = incremental.get_session("thread")
    with pytest.raises(SessionBindingError):
        await full.run("full history", session=session)


async def test_malformed_history_raises_history_error(tmp_path):
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path, history_mode="full"), runtime=Runtime())
    session = agent.create_session()
    await agent.run("first", session=session)
    session.state["maf_codex"][agent.id]["history"] = [{"role": [], "author": None, "text": "bad"}]
    with pytest.raises(HistoryMismatchError):
        await agent.run("continue", session=session)
