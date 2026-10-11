"""Live native MAF Agent checks: echo tool, handoff, and structured group manager."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_framework import FunctionTool
from agent_framework.orchestrations import GroupChatBuilder, HandoffBuilder

from maf_codex import CodexAgentConfig, CodexChatClient
from maf_codex.runtime import CodexRuntime


class CountingRuntime(CodexRuntime):
    calls = 0

    async def run(self, *args, **kwargs):
        type(self).calls += 1
        if type(self).calls > 10:
            raise RuntimeError("Live probe turn budget exceeded")
        return await super().run(*args, **kwargs)


def native_agent(workspace, name, instructions, **kwargs):
    config = CodexAgentConfig(cwd=workspace, timeout_seconds=120)
    chat = CodexChatClient(config, runtime=CountingRuntime(config))
    return chat.as_agent(
        name=name,
        instructions="Never use native Codex tools, shell, files, or search. " + instructions,
        **kwargs,
    )


async def main():
    root = Path(__file__).resolve().parents[1]
    workspace = root / ".state" / "chat-client-probe"
    workspace.mkdir(parents=True, exist_ok=True)
    marker = "probe-" + uuid4().hex
    calls = []

    def echo(text: str):
        calls.append(text)
        return "echo:" + text

    print("1/3: MAF tool execution through Codex structured output...", flush=True)
    agent = native_agent(
        workspace,
        "Echo",
        "If no echo function_result follows the latest user request, request MAF echo. "
        "If an echo function_result is present, return its exact text as a final message. "
        "Never request echo again when its result is already present.",
        tools=[FunctionTool(name="echo", func=echo)],
    )
    response = await agent.run(f"Echo {marker} using the MAF tool.")
    if calls != [marker] or response.text.strip() != "echo:" + marker:
        print({"calls": calls, "response": response.text}, flush=True)
        raise RuntimeError("Native MAF echo check failed")

    print("2/3: native HandoffBuilder routing...", flush=True)
    triage = native_agent(
        workspace,
        "Triage",
        "Always request the MAF handoff tool to Specialist. Do not answer yourself.",
        require_per_service_call_history_persistence=True,
    )
    specialist = native_agent(
        workspace,
        "Specialist",
        "Return only HANDOFF_OK. Do not request any tools.",
        require_per_service_call_history_persistence=True,
    )
    workflow = (
        HandoffBuilder(
            participants=[triage, specialist],
            termination_condition=lambda messages: (
                bool(messages) and messages[-1].text.strip() == "HANDOFF_OK"
            ),
        )
        .with_start_agent(triage)
        .build()
    )
    outputs = (await workflow.run("Transfer this task to Specialist.")).get_outputs()
    if not any(output.text.strip() == "HANDOFF_OK" for output in outputs):
        raise RuntimeError("Native handoff check failed")

    print("3/3: GroupChatBuilder agent manager structured output...", flush=True)
    writer = native_agent(workspace, "Writer", "Return only DRAFT_OK.")
    manager = native_agent(
        workspace,
        "Manager",
        "Select Writer if the conversation has no DRAFT_OK response. "
        "Once DRAFT_OK is present, terminate with final_message GROUP_OK.",
    )
    workflow = GroupChatBuilder(
        participants=[writer],
        orchestrator_agent=manager,
        max_rounds=3,
    ).build()
    outputs = (await workflow.run("Ask Writer to draft, then finish.")).get_outputs()
    if outputs[-1].text.strip() != "GROUP_OK":
        raise RuntimeError("Native group manager check failed")
    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "maf_tool_execution": "passed",
        "native_handoff": "passed",
        "native_group_manager": "passed",
        "live_turns": CountingRuntime.calls,
    }
    (root / ".state" / "chat-client-check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
