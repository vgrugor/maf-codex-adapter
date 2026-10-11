import asyncio

import pytest
from agent_framework import AgentSession, Content, FunctionTool
from openai_codex.generated.v2_all import DynamicToolSpec

from maf_codex.tools import ToolBridge, validate_tools


def request(**changes):
    return {
        "threadId": "thread",
        "turnId": "turn",
        "callId": "call",
        "tool": "maf_echo",
        "arguments": {"text": "hello"},
        **changes,
    }


def active_bridge(approval_mode="never_require", handler=None, func=None):
    calls = []

    def echo(text: str):
        calls.append(text)
        return text

    tool = FunctionTool(name="echo", func=func or echo, approval_mode=approval_mode)
    bridge = ToolBridge([tool], AgentSession(), handler)
    bridge.start_thread("thread")
    bridge.start_turn("turn")
    return bridge, calls


async def test_maf_function_maps_schema_arguments_and_text_result():
    bridge, calls = active_bridge()
    DynamicToolSpec.model_validate(bridge.specs[0])
    response = await bridge.handle("item/tool/call", request())
    assert response == {"success": True, "contentItems": [{"type": "inputText", "text": "hello"}]}
    assert calls == ["hello"]


@pytest.mark.parametrize(
    "params",
    [
        request(threadId="other"),
        request(turnId="other"),
        request(tool="missing"),
        request(namespace="other"),
        request(callId=""),
        request(arguments=[]),
        request(arguments={}),
        request(arguments={"text": "hello", "extra": 1}),
    ],
)
async def test_invalid_or_foreign_requests_never_execute(params):
    bridge, calls = active_bridge()
    assert not (await bridge.handle("item/tool/call", params))["success"]
    assert calls == []


async def test_duplicate_call_id_cannot_execute_twice():
    bridge, calls = active_bridge()
    assert (await bridge.handle("item/tool/call", request()))["success"]
    assert not (await bridge.handle("item/tool/call", request()))["success"]
    assert calls == ["hello"]


async def test_missing_handler_denies_required_approval():
    bridge, calls = active_bridge("always_require")
    assert not (await bridge.handle("item/tool/call", request()))["success"]
    assert not calls


@pytest.mark.parametrize(
    "decision,success",
    [
        ("approve", True),
        ("reject", False),
        ("cancel", False),
        ("acceptForSession", False),
        (None, False),
    ],
)
async def test_explicit_tool_approval_decisions(decision, success):
    observed = []

    async def handler(approval):
        observed.append(approval)
        return decision

    bridge, calls = active_bridge("always_require", handler)
    assert (await bridge.handle("item/tool/call", request()))["success"] is success
    assert len(calls) == int(success)
    assert observed[0].arguments == {"text": "hello"}
    assert observed[0].thread_id == "thread" and observed[0].turn_id == "turn"


async def test_delayed_approval_does_not_execute_before_answer():
    waiting, allow = asyncio.Event(), asyncio.Future()

    async def handler(approval):
        waiting.set()
        return await allow

    bridge, calls = active_bridge("always_require", handler)
    pending = asyncio.create_task(bridge.handle("item/tool/call", request()))
    await waiting.wait()
    assert calls == []
    allow.set_result("approve")
    assert (await pending)["success"]


async def test_mutating_approval_preview_cannot_change_tool_arguments():
    async def handler(approval):
        approval.arguments["text"] = "changed"
        return "approve"

    bridge, calls = active_bridge("always_require", handler)
    await bridge.handle("item/tool/call", request())
    assert calls == ["hello"]


async def test_handler_failure_is_a_denial():
    async def handler(approval):
        raise RuntimeError("private detail")

    bridge, calls = active_bridge("always_require", handler)
    result = await bridge.handle("item/tool/call", request())
    assert not result["success"] and not calls
    assert "private detail" not in str(result)


async def test_tool_exception_is_returned_without_sensitive_details():
    def fail(text: str):
        raise ValueError("private detail")

    bridge, _ = active_bridge(func=fail)
    result = await bridge.handle("item/tool/call", request())
    assert not result["success"]
    assert "ValueError" in str(result) and "private detail" not in str(result)


async def test_rich_media_result_is_explicitly_rejected():
    def image(text: str):
        return [Content.from_data(b"image", "image/png")]

    bridge, _ = active_bridge(func=image)
    assert not (await bridge.handle("item/tool/call", request()))["success"]


@pytest.mark.parametrize(
    "method,kind",
    [
        ("item/commandExecution/requestApproval", "command"),
        ("item/fileChange/requestApproval", "file"),
    ],
)
@pytest.mark.parametrize(
    "decision,wire", [("approve", "accept"), ("reject", "decline"), ("cancel", "cancel")]
)
async def test_native_approval_mapping(method, kind, decision, wire):
    observed = []

    async def handler(approval):
        observed.append(approval)
        return decision

    bridge, _ = active_bridge(handler=handler)
    result = await bridge.handle(
        method, {"threadId": "thread", "turnId": "turn", "itemId": "item", "command": "echo hello"}
    )
    assert result == {"decision": wire}
    assert observed[0].kind == kind


async def test_native_default_denies_and_respects_available_decisions():
    bridge, _ = active_bridge()
    params = {"threadId": "thread", "turnId": "turn", "itemId": "item"}
    assert await bridge.handle("item/commandExecution/requestApproval", params) == {
        "decision": "decline"
    }

    async def approve(approval):
        return "approve"

    bridge, _ = active_bridge(handler=approve)
    params["availableDecisions"] = ["decline", "cancel"]
    assert await bridge.handle("item/commandExecution/requestApproval", params) == {
        "decision": "decline"
    }


async def test_unsupported_permission_and_elicitation_requests_do_not_grant():
    bridge, _ = active_bridge()
    assert await bridge.handle("item/permissions/requestApproval", {}) == {
        "permissions": {},
        "scope": "turn",
    }
    assert await bridge.handle("mcpServer/elicitation/request", {}) == {
        "action": "cancel",
        "content": None,
    }
    with pytest.raises(NotImplementedError):
        await bridge.handle("item/tool/requestUserInput", {})


async def test_request_before_turn_reply_waits_for_scope_binding():
    bridge, calls = active_bridge()
    bridge.ready.clear()
    bridge.turn_id = None
    pending = asyncio.create_task(bridge.handle("item/tool/call", request()))
    await asyncio.sleep(0)
    assert not calls
    bridge.start_turn("turn")
    assert (await pending)["success"]


async def test_closed_scope_rejects_calls():
    bridge, calls = active_bridge()
    async with bridge:
        pass
    assert not (await bridge.handle("item/tool/call", request()))["success"]
    assert not calls


async def test_cancel_decision_interrupts_collection():
    bridge, _ = active_bridge()
    started = asyncio.Event()

    class Turn:
        async def run(self):
            started.set()
            await asyncio.Event().wait()

    pending = asyncio.create_task(bridge.collect(Turn()))
    await started.wait()
    bridge.cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        await pending


@pytest.mark.parametrize(
    "tools",
    [
        [object()],
        [FunctionTool(name="bad.name", func=lambda: "ok")],
        [
            FunctionTool(name="same", func=lambda: "ok"),
            FunctionTool(name="same", func=lambda: "ok"),
        ],
        [FunctionTool(name="declaration", input_model={"type": "object"})],
    ],
)
def test_unsupported_registries_fail_before_execution(tools):
    with pytest.raises((TypeError, ValueError)):
        validate_tools(tools)


async def test_distinct_native_prompts_for_same_item_are_not_conflated():
    approvals = []

    async def handler(request):
        approvals.append(request.request_id)
        return "approve"

    bridge, _ = active_bridge(handler=handler)
    params = {"threadId": "thread", "turnId": "turn", "itemId": "same-item"}
    for rpc_id in (1, 2):
        assert await bridge.handle(
            "item/commandExecution/requestApproval", params, rpc_request_id=rpc_id
        ) == {"decision": "accept"}
    assert approvals == ["1", "2"]
    assert await bridge.handle(
        "item/commandExecution/requestApproval", params, rpc_request_id=2
    ) == {"decision": "decline"}


async def test_normalized_arguments_are_approved_and_executed_consistently():
    approved, called = [], []

    def number(value: int):
        called.append(value)
        return str(value)

    async def handler(request):
        approved.append(request.arguments)
        return "approve"

    bridge = ToolBridge(
        [FunctionTool(name="number", func=number, approval_mode="always_require")],
        AgentSession(),
        handler,
    )
    bridge.start_thread("thread")
    bridge.start_turn("turn")
    result = await bridge.handle(
        "item/tool/call", request(tool="maf_number", arguments={"value": "7"})
    )
    assert result["success"] and approved == [{"value": 7}] and called == [7]


async def test_native_late_approval_cannot_accept_closed_scope():
    started, allow = asyncio.Event(), asyncio.Future()

    async def handler(request):
        started.set()
        return await allow

    bridge, _ = active_bridge(handler=handler)
    params = {"threadId": "thread", "turnId": "turn", "itemId": "item"}
    pending = asyncio.create_task(bridge.handle("item/fileChange/requestApproval", params))
    await started.wait()
    bridge.closed = True
    allow.set_result("approve")
    assert await pending == {"decision": "decline"}


async def test_native_approval_includes_item_preview_when_available():
    observed = []

    async def handler(request):
        observed.append(request.arguments)
        return "reject"

    bridge, _ = active_bridge(handler=handler)
    bridge.observe(
        "item/started",
        {
            "threadId": "thread",
            "turnId": "turn",
            "item": {
                "id": "item",
                "type": "fileChange",
                "changes": [{"path": "example.py", "diff": "+example"}],
            },
        },
    )
    await bridge.handle(
        "item/fileChange/requestApproval",
        {"threadId": "thread", "turnId": "turn", "itemId": "item"},
    )
    assert observed[0]["item"]["changes"][0]["diff"] == "+example"


async def test_unbound_missing_scope_does_not_wait_forever():
    bridge = ToolBridge.empty()
    assert await asyncio.wait_for(
        bridge.handle("item/fileChange/requestApproval", {}), timeout=0.1
    ) == {"decision": "decline"}
