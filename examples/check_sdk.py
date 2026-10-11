"""Check ChatGPT auth and conversation resumption with two small live turns."""

import argparse
import json
import os
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from openai_codex import Codex, CodexConfig, Sandbox
from openai_codex.types import TurnStatus


def require_chatgpt(codex: Codex) -> None:
    account = codex.account().account
    if account is None or account.root.type != "chatgpt":
        raise RuntimeError(
            "SDK requires ChatGPT sign-in. Run codex login; no API fallback is used."
        )


def check_completed(result) -> str:
    if result.status != TurnStatus.completed or not result.final_response:
        raise RuntimeError(f"Codex turn did not complete: {result.status.value}")
    return result.final_response.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-bin", help="Optional intentional override of the bundled runtime")
    args = parser.parse_args()

    if any(os.environ.get(name) for name in ("OPENAI_API_KEY", "CODEX_API_KEY")):
        raise RuntimeError(
            "Unset OPENAI_API_KEY and CODEX_API_KEY for this subscription-only check."
        )

    project = Path(__file__).resolve().parents[1]
    workspace = project / ".state" / "probe-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = CodexConfig(codex_bin=args.codex_bin, cwd=str(workspace))
    marker = f"maf-check-{uuid4().hex}"

    print("Starting SDK and checking ChatGPT authentication...", flush=True)
    with Codex(config) as codex:
        require_chatgpt(codex)
        thread = codex.thread_start(
            cwd=str(workspace),
            sandbox=Sandbox.read_only,
            developer_instructions=(
                "This is a conversation-memory check. Do not use tools, inspect files, "
                "or execute commands. Follow the requested output format exactly."
            ),
        )
        thread_id = thread.id
        print("Running the first turn...", flush=True)
        first = check_completed(thread.run(f"Remember this marker: {marker}. Reply only READY."))
        if first != "READY":
            raise RuntimeError("First turn did not return READY.")

    print("Restarting SDK, resuming the thread, and checking memory...", flush=True)
    with Codex(config) as codex:
        require_chatgpt(codex)
        resumed = codex.thread_resume(thread_id, sandbox=Sandbox.read_only, cwd=str(workspace))
        recalled = check_completed(resumed.run("Repeat the marker from my previous message, only."))
        if recalled != marker:
            raise RuntimeError("Resumed conversation did not recall the original marker.")

    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "maf_version": version("agent-framework-core"),
        "sdk_version": version("openai-codex"),
        "runtime": args.codex_bin or "sdk-bundled",
        "auth": "chatgpt",
        "first_turn": "passed",
        "restart_and_resume": "passed",
        "conversation_memory": "passed",
        "maf_adapter": "not_implemented",
    }
    report_path = project / ".state" / "sdk-check.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
