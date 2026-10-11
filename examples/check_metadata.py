"""Two live turns checking SDK metadata and usage after session restoration."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from agent_framework import AgentSession

from maf_codex import CodexAgent, CodexAgentConfig


async def main():
    root = Path(__file__).resolve().parents[1]
    workspace = root / ".state" / "metadata-probe"
    workspace.mkdir(parents=True, exist_ok=True)
    config = CodexAgentConfig(
        cwd=workspace, instructions="Do not use any tools. Return only requested text."
    )
    agent = CodexAgent(config, id="metadata-probe")
    session = agent.create_session()
    print("1/2: checking first response metadata...", flush=True)
    first = await agent.run("Reply only FIRST.", session=session)
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    replacement = CodexAgent(config, id=agent.id)
    print("2/2: checking restored metadata and buffered stream...", flush=True)
    second = await replacement.run(
        "Reply only SECOND.", session=restored, stream=True
    ).get_final_response()
    if first.text.strip() != "FIRST" or second.text.strip() != "SECOND":
        raise RuntimeError("Metadata probe response check failed")
    for response in (first, second):
        if response.additional_properties["codex"]["status"] != "completed":
            raise RuntimeError("Completion metadata missing")
    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "status_and_restore": "passed",
        "buffered_stream_metadata": "passed",
        "first_usage": first.usage_details,
        "second_usage": second.usage_details,
        "first_usage_scope": first.additional_properties["codex"]["usage_scope"],
        "second_usage_scope": second.additional_properties["codex"]["usage_scope"],
        "configured_model": second.additional_properties["codex"]["configured_model"],
        "live_turns": 2,
    }
    (root / ".state" / "metadata-check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
