import asyncio
import queue

from openai_codex import CodexConfig
from openai_codex.client import CodexClient

from maf_codex.sdk_bridge import BridgeTransport
from maf_codex.tools import ToolBridge


async def test_registration_and_approval_policy_are_injected_without_mutating_payload(monkeypatch):
    sent = []
    monkeypatch.setattr(CodexClient, "_write_message", lambda self, payload: sent.append(payload))
    bridge = ToolBridge.empty()
    transport = BridgeTransport(CodexConfig(), bridge)
    original = {"id": 1, "method": "thread/start", "params": {"cwd": "/tmp"}}
    transport._write_message(original)
    assert original["params"] == {"cwd": "/tmp"}
    assert sent[0]["params"]["dynamicTools"] == []
    assert sent[0]["params"]["approvalPolicy"] == "never"
    assert sent[0]["params"]["approvalsReviewer"] == "user"

    async def approve(request):
        return "approve"

    bridge.approval_handler = approve
    transport._write_message({"id": 2, "method": "thread/resume", "params": {"threadId": "thread"}})
    assert sent[1]["params"]["approvalPolicy"] == "on-request"
    assert "dynamicTools" not in sent[1]["params"]


async def test_delayed_server_request_does_not_block_stdout_router():
    bridge = ToolBridge.empty()
    bridge.start_thread("thread")
    bridge.start_turn("turn")
    approval_started, routed = asyncio.Event(), asyncio.Event()
    allow = asyncio.Future()

    async def approve(request):
        approval_started.set()
        return await allow

    bridge.approval_handler = approve
    transport = BridgeTransport(CodexConfig(), bridge)
    messages = queue.Queue()
    writes = []
    loop = asyncio.get_running_loop()

    class Router:
        def route_response(self, response):
            loop.call_soon_threadsafe(routed.set)

        def fail_all(self, exc):
            pass

    transport._router = Router()

    def read():
        item = messages.get(timeout=2)
        if isinstance(item, Exception):
            raise item
        return item

    transport._read_message = read
    transport._write_message = writes.append
    messages.put(
        {
            "id": "approve",
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread", "turnId": "turn", "itemId": "item", "command": "echo"},
        }
    )
    messages.put({"id": 22, "result": {}})
    reader = asyncio.create_task(asyncio.to_thread(transport._reader_loop))
    try:
        await asyncio.wait_for(approval_started.wait(), timeout=1)
        await asyncio.wait_for(routed.wait(), timeout=1)
        assert writes == []
        allow.set_result("approve")
        for future in tuple(transport.pending):
            await asyncio.wrap_future(future)
        await asyncio.sleep(0)
        assert writes[0] == {"id": "approve", "result": {"decision": "accept"}}
    finally:
        await transport.stop_requests()
        messages.put(RuntimeError("reader closed"))
        await reader


async def test_close_cancels_pending_handler_and_does_not_send_accept():
    bridge = ToolBridge.empty()
    bridge.start_thread("thread")
    bridge.start_turn("turn")
    started = asyncio.Event()

    async def approve(request):
        started.set()
        await asyncio.Event().wait()

    bridge.approval_handler = approve
    transport = BridgeTransport(CodexConfig(), bridge)
    writes = []
    transport._write_message = writes.append
    transport._dispatch(
        {
            "id": 1,
            "method": "item/fileChange/requestApproval",
            "params": {"threadId": "thread", "turnId": "turn", "itemId": "item"},
        }
    )
    await started.wait()
    await transport.stop_requests()
    assert bridge.closed
    assert writes and "error" in writes[0]
    transport._dispatch({"id": 2, "method": "item/tool/call", "params": {}})
    assert "error" in writes[-1] and not transport.pending


async def test_model_capture_does_not_break_routing_malformed_response():
    bridge = ToolBridge.empty()
    transport = BridgeTransport(CodexConfig(), bridge)
    transport.bootstraps = {1, 2}
    frames = iter(
        [
            {"id": 1, "result": {"thread": {"model": "model-a"}}},
            {"id": 2, "result": {"thread": "malformed"}},
        ]
    )
    observed = []

    class Router:
        def route_response(self, response):
            observed.append((response["id"], bridge.configured_model))

        def fail_all(self, exc):
            pass

    transport._router = Router()
    transport._read_message = lambda: next(frames)
    transport._reader_loop()
    assert observed == [(1, "model-a"), (2, None)]
