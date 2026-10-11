"""Two live turns: approved echo, then rejection after restoring the same session."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_framework import AgentSession, FunctionTool

from maf_codex import CodexAgent, CodexAgentConfig


async def main():
    root = Path(__file__).resolve().parents[1]
    workspace = root / ".state" / "tools-probe"
    workspace.mkdir(parents=True, exist_ok=True)
    calls, approvals = [], []
    marker = "tool-probe-" + uuid4().hex
    allowed = True

    def echo(text: str) -> str:
        calls.append(text)
        return "echo:" + text

    async def review(request):
        approvals.append(request)
        if request.kind != "tool" or request.name != "echo":
            return "reject"
        return "approve" if allowed else "reject"

    config = CodexAgentConfig(
        cwd=workspace,
        instructions=(
            "Use only the provided maf_echo tool when asked. Never use shell, files, "
            "search, or any other tools. Call it exactly once per request."
        ),
    )
    tool = FunctionTool(
        name="echo",
        description="Return the exact echo of text.",
        func=echo,
        approval_mode="always_require",
    )
    agent = CodexAgent(config, id="tools-probe", tools=[tool], approval_handler=review)
    session = agent.create_session()
    print("1/2: calling an approved MAF tool through real Codex...", flush=True)
    response = await agent.run(
        f"Call maf_echo with text {marker}. Return only its result.", session=session
    )
    if calls != [marker] or response.text.strip() != "echo:" + marker:
        raise RuntimeError("Approved tool check failed")
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    allowed = False
    replacement = CodexAgent(config, id=agent.id, tools=[tool], approval_handler=review)
    print("2/2: restoring the session and rejecting the next tool invocation...", flush=True)
    await replacement.run(
        f"Call maf_echo once with text denied-{marker}. If denied, report denial and stop.",
        session=restored,
    )
    if calls != [marker] or len(approvals) != 2:
        raise RuntimeError("Rejected tool or resume check failed")
    if (
        approvals[0].thread_id != approvals[1].thread_id
        or approvals[0].turn_id == approvals[1].turn_id
    ):
        raise RuntimeError("Approval scope check failed")
    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "approved_tool": "passed",
        "rejected_tool": "passed",
        "serialized_resume": "passed",
        "approval_scope": "passed",
        "live_turns": 2,
    }
    (root / ".state" / "tools-check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
