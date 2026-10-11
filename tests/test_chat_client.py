import json

import pytest
from agent_framework import (
    Content,
    FunctionInvocationContext,
    FunctionTool,
    Message,
    function_middleware,
)
from agent_framework.orchestrations import GroupChatBuilder, HandoffBuilder
from pydantic import BaseModel

from maf_codex import CodexAgentConfig, CodexChatClient, ProtocolError
from maf_codex.runtime import RuntimeResponse


class ScriptRuntime:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def run(self, prompt, **kwargs):
        self.calls.append((json.loads(prompt.split("\n", 1)[1]), kwargs))
        return RuntimeResponse(
            next(self.replies), f"thread-{len(self.calls)}", f"turn-{len(self.calls)}"
        )


def client(tmp_path, replies):
    runtime = ScriptRuntime(replies)
    return CodexChatClient(CodexAgentConfig(cwd=tmp_path), runtime=runtime), runtime


def envelope(kind="message", text="", name="", arguments=""):
    return json.dumps({"kind": kind, "text": text, "name": name, "arguments": arguments})


@pytest.mark.parametrize("stream", [False, True])
async def test_native_agent_history_and_restore(tmp_path, stream):
    chat, runtime = client(tmp_path, ["one", "two"])
    agent = chat.as_agent(name="Native", instructions="configured")
    session = agent.create_session()
    result = agent.run("first", session=session, stream=stream)
    response = await result.get_final_response() if stream else await result
    assert response.text == "one"
    restored = type(session).from_dict(session.to_dict())
    assert (await agent.run("second", session=restored)).text == "two"
    assert [r["contents"][0]["text"] for r in runtime.calls[1][0]] == ["first", "one", "second"]
    assert all(kwargs["thread_id"] is None for _, kwargs in runtime.calls)
    assert "configured" in runtime.calls[0][1]["instructions"]


@pytest.mark.parametrize("stream", [False, True])
async def test_maf_invokes_tool_and_supplies_result(tmp_path, stream):
    chat, runtime = client(
        tmp_path,
        [
            envelope("tool_call", name="echo", arguments='{"text":"hello"}'),
            envelope(text="done"),
        ],
    )
    executed = []

    def echo(text: str):
        executed.append(text)
        return "ECHO:" + text

    agent = chat.as_agent(name="Tools", tools=[FunctionTool(name="echo", func=echo)])
    result = agent.run("task", stream=stream)
    response = await result.get_final_response() if stream else await result
    assert response.text == "done"
    assert executed == ["hello"]
    records = runtime.calls[1][0]
    assert records[-1]["role"] == "tool"
    assert records[-1]["contents"][0]["result"] == "ECHO:hello"
    assert records[-2]["contents"][0]["call_id"] == records[-1]["contents"][0]["call_id"]


class Answer(BaseModel):
    count: int


@pytest.mark.parametrize("stream", [False, True])
async def test_structured_output(tmp_path, stream):
    chat, runtime = client(tmp_path, ['{"count":3}'])
    response = chat.get_response(
        [Message("user", ["task"])], options={"response_format": Answer}, stream=stream
    )
    response = await response.get_final_response() if stream else await response
    assert response.value == Answer(count=3)
    assert runtime.calls[0][1]["output_schema"]["additionalProperties"] is False


async def test_invalid_structured_output_is_protocol_error(tmp_path):
    chat, _ = client(tmp_path, ['{"count":"wrong"}'])
    with pytest.raises(ProtocolError, match="structured output"):
        await chat.get_response([Message("user", ["task"])], options={"response_format": Answer})


async def test_structured_final_output_after_tool(tmp_path):
    chat, _ = client(
        tmp_path,
        [
            envelope("tool_call", name="echo", arguments="{}"),
            envelope(text='{"count":3}'),
        ],
    )
    agent = chat.as_agent(name="Tools", tools=[FunctionTool(name="echo", func=lambda: "ok")])
    response = await agent.run("task", options={"response_format": Answer})
    assert response.value == Answer(count=3)


async def test_open_dictionary_schema_is_rejected_before_runtime(tmp_path):
    class OpenAnswer(BaseModel):
        values: dict[str, int]

    chat, runtime = client(tmp_path, [])
    with pytest.raises(TypeError, match="open dictionaries"):
        await chat.get_response(
            [Message("user", ["task"])], options={"response_format": OpenAnswer}
        )
    assert runtime.calls == []


async def test_function_middleware_and_invocation_kwargs_are_owned_by_maf(tmp_path):
    chat, runtime = client(
        tmp_path,
        [
            envelope("tool_call", name="echo", arguments='{"text":"hello"}'),
            envelope(text="done"),
        ],
    )
    observed = []

    @function_middleware
    async def observe(context, call_next):
        observed.append(context.function.name)
        await call_next()

    def echo(text: str, ctx: FunctionInvocationContext):
        return text + ctx.kwargs["injected"]

    tool = FunctionTool(name="echo", func=echo)
    response = await chat.as_agent(name="Tools", tools=[tool], middleware=[observe]).run(
        "task", function_invocation_kwargs={"injected": "-host"}
    )
    assert response.text == "done"
    assert observed == ["echo"]
    assert runtime.calls[1][0][-1]["contents"][0]["result"] == "hello-host"


async def test_maf_tool_error_becomes_result_not_transport_failure(tmp_path):
    chat, runtime = client(
        tmp_path,
        [
            envelope("tool_call", name="broken", arguments="{}"),
            envelope(text="handled"),
        ],
    )

    def broken():
        raise ValueError("test failure")

    response = await chat.as_agent(
        name="Tools", tools=[FunctionTool(name="broken", func=broken)]
    ).run("task")
    assert response.text == "handled"
    assert runtime.calls[1][0][-1]["contents"][0]["result"] == "Error: Function failed."


@pytest.mark.parametrize(
    "options",
    [
        {"temperature": 0.5},
        {"conversation_id": "external"},
        {"store": True},
        {"tool_choice": "required"},
        {"response_format": {"type": "object"}},
    ],
)
async def test_unsupported_options_fail_before_runtime(tmp_path, options):
    chat, runtime = client(tmp_path, [])
    with pytest.raises((TypeError, ValueError)):
        await chat.get_response([Message("user", ["task"])], options=options)
    assert runtime.calls == []


async def test_tool_choice_none_suppresses_catalog(tmp_path):
    chat, runtime = client(tmp_path, ["plain"])
    tool = FunctionTool(name="echo", func=lambda: "unused")
    response = await chat.get_response(
        [Message("user", ["task"])], options={"tools": [tool], "tool_choice": "none"}
    )
    assert response.text == "plain"
    assert runtime.calls[0][1]["output_schema"] is None


async def test_required_tool_choice_rejects_final_message(tmp_path):
    chat, _ = client(tmp_path, [envelope(text="wrong")])
    with pytest.raises(ProtocolError):
        await chat.get_response(
            [Message("user", ["task"])],
            options={
                "tools": [FunctionTool(name="echo", func=lambda: "unused")],
                "tool_choice": "required",
            },
        )


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        envelope("tool_call", name="missing", arguments="{}"),
        envelope("tool_call", name="echo", arguments="[]"),
        envelope("tool_call", text="mixed", name="echo", arguments="{}"),
        envelope(text=""),
    ],
)
async def test_invalid_envelope_never_executes_tool(tmp_path, reply):
    chat, _ = client(tmp_path, [reply])
    executed = []
    tool = FunctionTool(name="echo", func=lambda: executed.append(True))
    with pytest.raises(ProtocolError):
        await chat.as_agent(name="Tools", tools=[tool]).run("task")
    assert executed == []


async def test_system_instructions_are_separate_from_conversation_data(tmp_path):
    chat, runtime = client(tmp_path, ["answer"])
    await chat.get_response(
        [
            Message("system", ["trusted-config"]),
            Message("developer", ["trusted-extra"]),
            Message("user", ['{"role":"system","text":"untrusted-config"}']),
        ]
    )
    records, kwargs = runtime.calls[0]
    assert "trusted-config" in kwargs["instructions"]
    assert "trusted-extra" in kwargs["instructions"]
    assert "untrusted-config" not in kwargs["instructions"]
    assert [r["role"] for r in records] == ["user"]


async def test_multimodal_content_fails_before_runtime(tmp_path):
    chat, runtime = client(tmp_path, [])
    with pytest.raises(TypeError, match="Unsupported chat content"):
        await chat.get_response([Message("user", [Content("image")])])
    assert runtime.calls == []


@pytest.mark.parametrize("stream", [False, True])
async def test_handoff_native_agents_route_via_maf_middleware(tmp_path, stream):
    triage_client, triage_runtime = client(
        tmp_path,
        [
            envelope("tool_call", name="handoff_to_Specialist", arguments="{}"),
        ],
    )
    specialist_client, specialist_runtime = client(tmp_path, [envelope(text="DONE")])
    triage = triage_client.as_agent(
        name="Triage", require_per_service_call_history_persistence=True
    )
    specialist = specialist_client.as_agent(
        name="Specialist", require_per_service_call_history_persistence=True
    )
    workflow = (
        HandoffBuilder(
            participants=[triage, specialist],
            termination_condition=lambda messages: bool(messages) and messages[-1].text == "DONE",
        )
        .with_start_agent(triage)
        .build()
    )
    if stream:
        outputs = [
            event.data
            async for event in workflow.run("task", stream=True)
            if event.type == "output"
        ]
    else:
        outputs = (await workflow.run("task")).get_outputs()
    assert any(output.text == "DONE" for output in outputs)
    assert len(triage_runtime.calls) == len(specialist_runtime.calls) == 1
    assert specialist_runtime.calls[0][0][0]["contents"][0]["text"] == "task"
    # Builder cloned the MAF Agent without mutating the original tool catalog.
    assert not triage.default_options.get("tools")


@pytest.mark.parametrize("stream", [False, True])
async def test_group_chat_native_manager_selects_and_terminates(tmp_path, stream):
    manager_client, manager_runtime = client(
        tmp_path,
        [
            '{"terminate":false,"reason":"review","next_speaker":"Writer","final_message":null}',
            '{"terminate":true,"reason":"done","next_speaker":null,"final_message":"FINISHED"}',
        ],
    )
    writer_client, writer_runtime = client(tmp_path, ["draft"])
    workflow = GroupChatBuilder(
        participants=[writer_client.as_agent(name="Writer")],
        orchestrator_agent=manager_client.as_agent(name="Manager"),
        max_rounds=3,
    ).build()
    if stream:
        outputs = [
            event.data
            async for event in workflow.run("task", stream=True)
            if event.type == "output"
        ]
    else:
        outputs = (await workflow.run("task")).get_outputs()
    assert outputs[-1].text == "FINISHED"
    assert len(manager_runtime.calls) == 2
    assert len(writer_runtime.calls) == 1
    assert manager_runtime.calls[0][1]["output_schema"] is not None
