import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from agent_framework import AgentSession
from openai_codex.errors import InvalidParamsError, TransportClosedError
from openai_codex.errors import ServerBusyError as SDKBusy
from openai_codex.types import TurnStatus

from maf_codex import (
    CodexAgent,
    CodexAgentConfig,
    ProtocolError,
    ServiceBusyError,
    SessionBindingError,
    ThreadBusyError,
    TransportError,
    TurnFailedError,
)
from maf_codex.metadata import counter_delta, item_summaries
from maf_codex.runtime import CodexRuntime, RuntimeResponse


def report(inputs=100, outputs=20):
    return {
        "total": {
            "input_tokens": inputs,
            "output_tokens": outputs,
            "total_tokens": inputs + outputs,
            "cached_input_tokens": 10,
            "cache_write_input_tokens": None,
            "reasoning_output_tokens": 5,
        },
        "last": {"input_tokens": 70, "output_tokens": 10, "total_tokens": 80},
    }


class ReportingRuntime:
    def __init__(self):
        self.count = 0
        self.fail = False

    async def run(self, prompt, *, thread_id, bind_thread, **kwargs):
        bind_thread(thread_id or "thread")
        self.count += 1
        if self.fail:
            raise TurnFailedError("failed")
        return RuntimeResponse(
            "answer",
            thread_id or "thread",
            "turn",
            usage=report(100 * self.count, 20 * self.count),
            metadata={
                "status": "completed",
                "items": [{"type": "dynamicToolCall", "success": False}],
            },
        )


@pytest.mark.parametrize("stream", [False, True])
async def test_usage_is_counter_delta_and_survives_response_stream(tmp_path, stream):
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=ReportingRuntime())
    session = agent.create_session()
    first = agent.run("first", session=session, stream=stream)
    response = await first.get_final_response() if stream else await first
    assert response.usage_details["input_token_count"] == 100
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    second = agent.run("second", session=restored, stream=stream)
    response = await second.get_final_response() if stream else await second
    assert response.usage_details["input_token_count"] == 100
    assert response.usage_details["total_token_count"] == 120
    assert response.additional_properties["codex"]["usage_report"]["total"]["input_tokens"] == 200
    assert response.additional_properties["codex"]["items"][0]["success"] is False


async def test_imported_thread_usage_is_unknown_not_session_total(tmp_path):
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=ReportingRuntime())
    response = await agent.run("next", session=agent.get_session("external"))
    assert response.usage_details is None
    assert response.additional_properties["codex"]["usage_scope"] == "unknown"


async def test_failed_run_invalidates_usage_baseline(tmp_path):
    runtime = ReportingRuntime()
    agent = CodexAgent(CodexAgentConfig(cwd=tmp_path), runtime=runtime)
    session = agent.create_session()
    await agent.run("first", session=session)
    runtime.fail = True
    with pytest.raises(TurnFailedError):
        await agent.run("failed", session=session)
    runtime.fail = False
    response = await agent.run("recovery", session=session)
    assert response.usage_details is None
    assert (await agent.run("next", session=session)).usage_details["input_token_count"] == 100


def test_counter_reset_or_missing_baseline_is_not_fabricated_usage():
    assert counter_delta(report(), None, fresh=False) is None
    assert counter_delta(report(), report(200, 40)["total"], fresh=False) is None
    assert counter_delta(None, None, fresh=True) is None
    assert "cache_creation_input_token_count" not in counter_delta(report(), None, fresh=True)


def test_item_summaries_exclude_arguments_outputs_diffs_and_reasoning():
    raw = [
        {
            "id": "tool",
            "type": "dynamicToolCall",
            "tool": "echo",
            "success": False,
            "arguments": {"private": "value"},
            "contentItems": [{"text": "private"}],
        },
        {"id": "reason", "type": "reasoning", "content": "private"},
    ]
    summaries = item_summaries(raw)
    assert summaries == [
        {"id": "tool", "type": "dynamicToolCall", "tool": "echo", "success": False},
        {"id": "reason", "type": "reasoning"},
    ]


class Client:
    def __init__(self, error=None, close_error=None):
        self.error, self.close_error = error, close_error
        self.closed = False
        self.entered = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.wait = False
        self.model = "model-a"
        self.bridge = None
        self.turn_count = 0

    def install_bridge(self, bridge):
        self.bridge = bridge

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *args):
        self.closed = True
        if self.close_error:
            raise self.close_error

    async def account(self):
        if self.error:
            raise self.error
        return NS(account=NS(root=NS(type="chatgpt")))

    async def thread_start(self, **kwargs):
        return NS(id="thread", turn=self.turn)

    async def thread_resume(self, thread_id, **kwargs):
        if self.bridge:
            self.bridge.configured_model = self.model
        return NS(id=thread_id, turn=self.turn)

    async def turn(self, prompt):
        self.turn_count += 1
        return NS(id="turn", run=self.collect, interrupt=self.interrupt)

    async def interrupt(self):
        pass

    async def collect(self):
        self.started.set()
        if self.wait:
            await self.release.wait()
        return NS(
            id="turn",
            status=TurnStatus.completed,
            final_response="answer",
            usage=report(),
            duration_ms=10,
            items=[{"id": "tool", "type": "dynamicToolCall", "success": False}],
        )


@pytest.fixture
def sdk_environment(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)


@pytest.mark.parametrize(
    "native,expected",
    [
        (TransportClosedError("private details"), TransportError),
        (SDKBusy(-32001, "private details"), ServiceBusyError),
        (InvalidParamsError(-32602, "private details"), ProtocolError),
        (FileNotFoundError("private details"), TransportError),
    ],
)
async def test_sdk_error_categories_preserve_cause_without_copying_message(
    tmp_path, sdk_environment, native, expected
):
    client = Client(error=native)
    runtime = CodexRuntime(CodexAgentConfig(cwd=tmp_path), client_factory=lambda _: client)
    with pytest.raises(expected) as caught:
        await runtime.run("task", thread_id=None, bind_thread=lambda _: None)
    assert caught.value.__cause__ is native
    assert "private details" not in str(caught.value)
    assert client.closed


async def test_cleanup_failure_does_not_mask_primary_error(tmp_path, sdk_environment):
    client = Client(error=InvalidParamsError(-32602, "primary"), close_error=OSError("cleanup"))
    runtime = CodexRuntime(CodexAgentConfig(cwd=tmp_path), client_factory=lambda _: client)
    with pytest.raises(ProtocolError) as caught:
        await runtime.run("task", thread_id=None, bind_thread=lambda _: None)
    assert isinstance(caught.value.__cause__, InvalidParamsError)


async def test_distinct_sessions_cannot_overlap_one_provider_thread(tmp_path, sdk_environment):
    first, second = Client(), Client()
    first.wait = True
    config = CodexAgentConfig(cwd=tmp_path)
    a = CodexAgent(config, runtime=CodexRuntime(config, client_factory=lambda _: first))
    b = CodexAgent(config, runtime=CodexRuntime(config, client_factory=lambda _: second))
    one = asyncio.create_task(a.run("first", session=a.get_session("shared")))
    await first.started.wait()
    try:
        with pytest.raises(ThreadBusyError):
            await b.run("second", session=b.get_session("shared"))
        assert not second.entered
    finally:
        first.release.set()
        await one
    assert (await b.run("after", session=b.get_session("shared"))).text == "answer"


async def test_resume_model_drift_is_blocked_before_turn(tmp_path, sdk_environment):
    from maf_codex.tools import ToolBridge

    bridge = ToolBridge.empty()
    bridge.expected_model = "model-a"
    client = Client()
    client.model = "model-b"
    runtime = CodexRuntime(CodexAgentConfig(cwd=tmp_path), client_factory=lambda _: client)
    with pytest.raises(SessionBindingError):
        await runtime.run("task", thread_id="thread", bind_thread=lambda _: None, bridge=bridge)
    assert client.turn_count == 0 and client.closed


@pytest.mark.parametrize(
    "values",
    [
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": True},
        {"instructions": None},
        {"model": ""},
        {"sandbox": "read-only"},
        {"codex_bin": ""},
    ],
)
def test_config_rejects_invalid_values(tmp_path, values):
    with pytest.raises((TypeError, ValueError)):
        CodexAgentConfig(cwd=tmp_path, **values)
