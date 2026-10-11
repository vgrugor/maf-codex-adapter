"""Public adapter boundaries documented in docs/contract.md; no live Codex calls."""

import json

import pytest
from agent_framework import Message

from maf_codex import CodexAgent, CodexAgentConfig, SessionBindingError
from maf_codex.mapping import to_prompt
from maf_codex.runtime import RuntimeResponse


class RecordingRuntime:
    def __init__(self):
        self.prompts = []

    async def run(self, prompt, *, thread_id, bind_thread):
        self.prompts.append(prompt)
        bind_thread("thread-contract")
        return RuntimeResponse("unchanged answer\n", "thread-contract", "turn-contract")


@pytest.fixture
def agent(tmp_path):
    return CodexAgent(
        CodexAgentConfig(cwd=tmp_path),
        id="contract-agent",
        name="ContractAgent",
        runtime=RecordingRuntime(),
    )


def test_single_user_text_is_not_reformatted():
    text = "  збережи формат\n"
    assert to_prompt(Message("user", [text])) == text


def test_multiple_messages_preserve_roles_authors_and_text():
    prompt = to_prompt(
        [
            Message("user", ["запит"]),
            Message("assistant", ["відповідь"], author_name="Reviewer"),
        ]
    )
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload == [
        {"role": "user", "author": None, "text": "запит"},
        {"role": "assistant", "author": "Reviewer", "text": "відповідь"},
    ]


def test_single_assistant_message_keeps_role_label():
    prompt = to_prompt(Message("assistant", ["review"], author_name="Reviewer"))
    assert json.loads(prompt.split("\n", 1)[1])[0]["role"] == "assistant"


@pytest.mark.parametrize("messages", [[], " \n ", Message("developer", ["instructions"])])
def test_invalid_input_fails_synchronously_without_calling_runtime(agent, messages):
    with pytest.raises(ValueError):
        agent.run(messages)
    assert agent._runtime.prompts == []


@pytest.mark.parametrize("key", ["client_kwargs", "function_invocation_kwargs"])
@pytest.mark.parametrize("value", [None, {}])
async def test_empty_maf_invocation_options_are_accepted(agent, key, value):
    response = await agent.run("hello", **{key: value})
    assert response.text == "unchanged answer\n"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"client_kwargs": {"model": "override"}},
        {"function_invocation_kwargs": {"value": 1}},
        {"response_format": "json"},
    ],
)
def test_unimplemented_run_options_are_never_silently_ignored(agent, kwargs):
    with pytest.raises(TypeError):
        agent.run("hello", **kwargs)
    assert agent._runtime.prompts == []


@pytest.mark.parametrize("stream", [False, True])
async def test_final_metadata_matches_codex_identifiers_in_both_modes(agent, stream):
    output = agent.run("hello", stream=stream)
    response = await output.get_final_response() if stream else await output
    assert response.text == "unchanged answer\n"
    assert response.agent_id == "contract-agent"
    assert response.response_id == "turn-contract"
    assert response.messages[0].author_name == "ContractAgent"
    assert response.additional_properties["codex_thread_id"] == "thread-contract"
    assert response.usage_details is None


@pytest.mark.parametrize(
    "namespace",
    [
        "invalid",
        {"contract-agent": {}},
        {"contract-agent": {"thread_id": "", "fingerprint": "invalid"}},
    ],
)
async def test_invalid_session_state_is_rejected_before_runtime(agent, namespace):
    session = agent.create_session()
    session.state["maf_codex"] = namespace
    with pytest.raises(SessionBindingError):
        await agent.run("hello", session=session)
    assert agent._runtime.prompts == []
