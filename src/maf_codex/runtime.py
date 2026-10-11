"""Async SDK boundary. No MAF types or authentication secrets are stored here."""

import asyncio
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from openai_codex import AsyncCodex, CodexConfig
from openai_codex.types import TurnStatus

from .config import CodexAgentConfig
from .errors import AuthenticationError, TurnFailedError


@dataclass(frozen=True)
class RuntimeResponse:
    text: str
    thread_id: str
    turn_id: str


class CodexRuntime:
    """Create a SDK process per run and resume provider-owned conversation state."""

    def __init__(self, config: CodexAgentConfig, *, client_factory=AsyncCodex):
        self.config = config
        self._client_factory = client_factory

    async def run(
        self, prompt: str, *, thread_id: str | None, bind_thread: Callable[[str], None]
    ) -> RuntimeResponse:
        if any(os.environ.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY")):
            raise AuthenticationError(
                "Unset OPENAI_API_KEY and CODEX_API_KEY to use ChatGPT sign-in"
            )
        sdk_config = CodexConfig(cwd=str(self.config.cwd), codex_bin=self.config.codex_bin)
        async with asyncio.timeout(self.config.timeout_seconds):
            async with self._client_factory(sdk_config) as codex:
                account = (await codex.account()).account
                if account is None or account.root.type != "chatgpt":
                    raise AuthenticationError("ChatGPT sign-in is required; run codex login")
                options = {
                    "cwd": str(self.config.cwd),
                    "sandbox": self.config.sandbox,
                    "model": self.config.model,
                    "model_provider": "openai",
                    "developer_instructions": self.config.instructions or None,
                }
                if thread_id is None:
                    thread = await codex.thread_start(**options)
                else:
                    thread = await codex.thread_resume(thread_id, **options)
                # Persist the binding even if the turn fails; don't silently lose context.
                bind_thread(thread.id)
                turn = await thread.turn(prompt)
                try:
                    result = await turn.run()
                except asyncio.CancelledError:
                    with suppress(Exception):
                        await asyncio.wait_for(asyncio.shield(turn.interrupt()), timeout=5)
                    raise
                if result.status != TurnStatus.completed:
                    raise TurnFailedError(f"Codex turn ended with status {result.status.value}")
                if not result.final_response:
                    raise TurnFailedError("Codex completed without a text response")
                return RuntimeResponse(result.final_response, thread.id, result.id)
