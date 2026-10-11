import asyncio
from types import SimpleNamespace as NS

import pytest
from openai_codex.types import TurnStatus

from maf_codex import AuthenticationError, CodexAgentConfig, TurnFailedError
from maf_codex.runtime import CodexRuntime


class FakeClient:
    def __init__(self, config):
        self.auth = "chatgpt"
        self.status = TurnStatus.completed
        self.text = "ok"
        self.closed = False
        self.interrupted = False
        self.started = []
        self.resumed = []
        self.block = False
        self.running = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def account(self):
        return NS(account=NS(root=NS(type=self.auth)) if self.auth else None)

    async def thread_start(self, **kwargs):
        self.started.append(kwargs)
        return NS(id="new-thread", turn=self.turn)

    async def thread_resume(self, thread_id, **kwargs):
        self.resumed.append((thread_id, kwargs))
        return NS(id=thread_id, turn=self.turn)

    async def turn(self, prompt):
        return NS(run=self.collect, interrupt=self.interrupt)

    async def collect(self):
        self.running.set()
        if self.block:
            await asyncio.Event().wait()
        return NS(status=self.status, final_response=self.text, id="turn")

    async def interrupt(self):
        self.interrupted = True


@pytest.fixture
def setup_runtime(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    config = CodexAgentConfig(cwd=tmp_path, timeout_seconds=1)
    client = FakeClient(None)
    runtime = CodexRuntime(config, client_factory=lambda _: client)
    return runtime, client


@pytest.mark.parametrize("auth", [None, "apiKey", "amazonBedrock"])
async def test_non_chatgpt_auth_never_starts_turn(setup_runtime, auth):
    runtime, client = setup_runtime
    client.auth = auth
    with pytest.raises(AuthenticationError):
        await runtime.run("hello", thread_id=None, bind_thread=lambda _: None)
    assert not client.started
    assert client.closed


@pytest.mark.parametrize("key", ["OPENAI_API_KEY", "CODEX_API_KEY"])
async def test_api_environment_is_rejected(setup_runtime, monkeypatch, key):
    runtime, _ = setup_runtime
    monkeypatch.setenv(key, "test-placeholder")
    with pytest.raises(AuthenticationError):
        await runtime.run("hello", thread_id=None, bind_thread=lambda _: None)


async def test_start_and_resume_pass_configuration_and_bind_thread(setup_runtime):
    runtime, client = setup_runtime
    bindings = []
    first = await runtime.run("hello", thread_id=None, bind_thread=bindings.append)
    second = await runtime.run("continue", thread_id=first.thread_id, bind_thread=bindings.append)
    assert second.thread_id == first.thread_id
    assert bindings == ["new-thread", "new-thread"]
    assert client.started[0]["sandbox"] == runtime.config.sandbox
    assert client.resumed[0][0] == "new-thread"
    assert client.closed


async def test_per_run_instructions_are_reset_on_next_resume(setup_runtime):
    runtime, client = setup_runtime
    await runtime.run("first", thread_id=None, bind_thread=lambda _: None, instructions="once")
    await runtime.run("next", thread_id="new-thread", bind_thread=lambda _: None)
    assert client.started[0]["developer_instructions"] == "once"
    assert client.resumed[0][1]["developer_instructions"] == ""


async def test_runtime_passes_output_schema_to_turn(setup_runtime):
    runtime, client = setup_runtime
    original = client.turn
    received = []

    async def turn(prompt, **kwargs):
        received.append(kwargs)
        return await original(prompt)

    client.turn = turn
    schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    await runtime.run("task", thread_id=None, bind_thread=lambda _: None, output_schema=schema)
    assert received == [{"output_schema": schema}]


@pytest.mark.parametrize(
    "status,text",
    [(TurnStatus.failed, "ok"), (TurnStatus.interrupted, "ok"), (TurnStatus.completed, None)],
)
async def test_incomplete_turn_is_not_returned_as_success(setup_runtime, status, text):
    runtime, client = setup_runtime
    client.status, client.text = status, text
    with pytest.raises(TurnFailedError):
        await runtime.run("hello", thread_id=None, bind_thread=lambda _: None)


async def test_cancellation_interrupts_turn_and_closes_client(setup_runtime):
    runtime, client = setup_runtime
    client.block = True
    task = asyncio.create_task(runtime.run("hello", thread_id=None, bind_thread=lambda _: None))
    await client.running.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.interrupted
    assert client.closed


async def test_timeout_interrupts_turn(setup_runtime):
    runtime, client = setup_runtime
    client.block = True
    with pytest.raises(TimeoutError):
        await runtime.run("hello", thread_id=None, bind_thread=lambda _: None)
    assert client.interrupted
    assert client.closed
