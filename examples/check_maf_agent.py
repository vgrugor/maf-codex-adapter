"""Four live turns: MAF workflow, session isolation, and serialized restoration."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_framework import AgentExecutor, AgentSession, WorkflowBuilder

from maf_codex import CodexAgent, CodexAgentConfig


async def main() -> None:
    project = Path(__file__).resolve().parents[1]
    workspace = project / ".state" / "probe-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = CodexAgentConfig(
        cwd=workspace,
        instructions=(
            "This is a conversation-memory check. Do not use tools, inspect files, "
            "or run commands. Return only the requested text."
        ),
    )
    agent = CodexAgent(config, id="memory-check", name="MemoryCheck")
    session_a, session_b = agent.create_session(), agent.create_session()
    marker_a, marker_b = f"alpha-{uuid4().hex}", f"beta-{uuid4().hex}"

    executor = AgentExecutor(agent, session=session_a)
    workflow = WorkflowBuilder(start_executor=executor, output_from=[executor]).build()
    print("1/4: invoking CodexAgent through MAF WorkflowBuilder...", flush=True)
    result = await workflow.run(f"Remember my marker: {marker_a}. Reply only READY.")
    if not any(output.text.strip() == "READY" for output in result.get_outputs()):
        raise RuntimeError("Workflow did not return READY")

    print("2/4: storing a different marker in an independent session...", flush=True)
    response = await agent.run(
        f"Remember my marker: {marker_b}. Reply only READY.", session=session_b
    )
    if response.text.strip() != "READY":
        raise RuntimeError("Second session did not return READY")

    snapshot = json.loads(json.dumps(session_a.to_dict()))
    restored = AgentSession.from_dict(snapshot)
    replacement = CodexAgent(config, id=agent.id, name=agent.name)
    print("3/4: restoring a serialized MAF session with a new agent...", flush=True)
    recalled_a = await replacement.run("Repeat my marker, only.", session=restored)
    if recalled_a.text.strip() != marker_a:
        raise RuntimeError("Restored session lost its original context")

    print("4/4: checking that the second session kept its own context...", flush=True)
    recalled_b = await agent.run("Repeat my marker, only.", session=session_b)
    if recalled_b.text.strip() != marker_b:
        raise RuntimeError("Session isolation check failed")

    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "auth": "chatgpt",
        "maf_workflow": "passed",
        "independent_sessions": "passed",
        "serialized_session_restoration": "passed",
        "live_turns": 4,
    }
    (project / ".state" / "maf-agent-check.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
