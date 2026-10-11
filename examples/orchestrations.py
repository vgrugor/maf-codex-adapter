"""Run a small MAF orchestration with ChatGPT auth; consumes Codex plan usage."""

import argparse
import asyncio
from pathlib import Path

from agent_framework.orchestrations import ConcurrentBuilder, GroupChatBuilder, SequentialBuilder

from maf_codex import CodexAgent, CodexAgentConfig


def build_workflow(pattern, cwd):
    # Group Chat supplies deltas; Sequential supplies full conversation context.
    history_mode = "incremental" if pattern == "group" else "full"
    writer = CodexAgent(
        CodexAgentConfig(
            cwd=cwd,
            history_mode=history_mode,
            instructions="Propose a concise design. Review feedback when provided. Do not use tools.",
        ),
        id="writer",
        name="Writer",
    )
    reviewer = CodexAgent(
        CodexAgentConfig(
            cwd=cwd,
            history_mode=history_mode,
            instructions="Review the design concisely and identify one improvement. Do not use tools.",
        ),
        id="reviewer",
        name="Reviewer",
    )
    participants = [writer, reviewer]
    if pattern == "sequential":
        return SequentialBuilder(participants=participants).build()
    if pattern == "concurrent":
        return ConcurrentBuilder(participants=participants).build()
    return GroupChatBuilder(
        participants=participants,
        selection_func=lambda state: ["Writer", "Reviewer", "Writer"][state.current_round],
        max_rounds=3,
    ).build()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pattern", choices=["sequential", "concurrent", "group"])
    parser.add_argument("--cwd", type=Path, required=True, help="Target project outside this repo")
    parser.add_argument(
        "--task", default="Propose a small reliable task queue for a Python service."
    )
    args = parser.parse_args()
    workflow = build_workflow(args.pattern, args.cwd)
    result = await workflow.run(args.task)
    for output in result.get_outputs():
        print(output.text)


if __name__ == "__main__":
    asyncio.run(main())
