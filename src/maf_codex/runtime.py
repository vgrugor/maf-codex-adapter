"""Async SDK boundary. No MAF types or authentication secrets are stored here."""

import asyncio
import os
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from openai_codex import AsyncCodex, CodexConfig
from openai_codex.errors import (
    CodexError,
    JsonRpcError,
    TransportClosedError,
)
from openai_codex.errors import (
    ServerBusyError as SDKBusy,
)
from openai_codex.types import TurnStatus
from pydantic import ValidationError

from .config import CodexAgentConfig
from .errors import (
    AuthenticationError,
    ProtocolError,
    ServiceBusyError,
    SessionBindingError,
    TransportError,
    TurnFailedError,
)
from .metadata import item_summaries, usage_report
from .sdk_bridge import BridgedCodex
from .sessions import thread_lease
from .tools import ToolBridge


@dataclass(frozen=True)
class RuntimeResponse:
    text: str
    thread_id: str
    turn_id: str
    usage: dict | None = None
    metadata: dict = field(default_factory=dict)


@asynccontextmanager
async def sdk_session(client):
    try:
        await client.__aenter__()
        yield client
    except BaseException:
        with suppress(Exception):
            await client.__aexit__(*sys.exc_info())
        raise
    else:
        await client.__aexit__(None, None, None)


class CodexRuntime:
    """Create a SDK process per run and resume provider-owned conversation state."""

    def __init__(self, config: CodexAgentConfig, *, client_factory=AsyncCodex):
        self.config = config
        self._client_factory = client_factory

    async def run(
        self,
        prompt: str,
        *,
        thread_id: str | None,
        bind_thread: Callable[[str], None],
        instructions: str | None = None,
        bridge: ToolBridge | None = None,
        output_schema: dict | None = None,
    ) -> RuntimeResponse:
        try:
            return await self._run(
                prompt,
                thread_id=thread_id,
                bind_thread=bind_thread,
                instructions=instructions,
                bridge=bridge,
                output_schema=output_schema,
            )
        except TransportClosedError as exc:
            raise TransportError("Codex SDK transport closed") from exc
        except SDKBusy as exc:
            raise ServiceBusyError(
                "Codex service is busy; no automatic retry was attempted"
            ) from exc
        except (JsonRpcError, CodexError, ValidationError) as exc:
            error = ProtocolError("Codex SDK protocol operation failed")
            error.rpc_code = getattr(exc, "code", None)
            raise error from exc
        except TimeoutError:
            raise
        except OSError as exc:
            raise TransportError("Codex runtime could not start or perform transport IO") from exc

    async def _run(
        self, prompt, *, thread_id, bind_thread, instructions=None, bridge=None, output_schema=None
    ):
        if any(os.environ.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY")):
            raise AuthenticationError(
                "Unset OPENAI_API_KEY and CODEX_API_KEY to use ChatGPT sign-in"
            )
        sdk_config = CodexConfig(cwd=str(self.config.cwd), codex_bin=self.config.codex_bin)
        bridge = bridge or ToolBridge.empty()
        client = (
            BridgedCodex(sdk_config, bridge)
            if self._client_factory is AsyncCodex
            else self._client_factory(sdk_config)
        )
        if self._client_factory is not AsyncCodex and (
            bridge.tools or bridge.approval_handler or bridge.expected_model
        ):
            if not hasattr(client, "install_bridge"):
                raise TypeError("Injected SDK clients must implement install_bridge")
            client.install_bridge(bridge)
        async with (
            thread_lease(thread_id),
            bridge,
            asyncio.timeout(self.config.timeout_seconds),
            sdk_session(client) as codex,
        ):
            account = (await codex.account()).account
            if account is None or account.root.type != "chatgpt":
                raise AuthenticationError("ChatGPT sign-in is required; run codex login")
            options = {
                "cwd": str(self.config.cwd),
                "sandbox": self.config.sandbox,
                "model": self.config.model,
                "model_provider": "openai",
                "developer_instructions": instructions
                if instructions is not None
                else self.config.instructions,
            }
            if thread_id is None:
                thread = await codex.thread_start(**options)
            else:
                thread = await codex.thread_resume(thread_id, **options)
            if bridge.expected_model and bridge.configured_model != bridge.expected_model:
                raise SessionBindingError(
                    "Configured Codex model changed or could not be verified on resume"
                )
            async with thread_lease(thread.id if thread_id is None else None):
                return await self._collect(thread, prompt, bind_thread, bridge, output_schema)

    async def _collect(self, thread, prompt, bind_thread, bridge, output_schema=None):
        # Persist the binding even if the turn fails; don't silently lose context.
        bind_thread(thread.id)
        bridge.start_thread(thread.id)
        turn = await thread.turn(
            prompt, **({"output_schema": output_schema} if output_schema else {})
        )
        if hasattr(turn, "id"):
            bridge.start_turn(turn.id)
        try:
            result = (
                await bridge.collect(turn)
                if bridge.tools or bridge.approval_handler
                else await turn.run()
            )
        except asyncio.CancelledError:
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(turn.interrupt()), timeout=5)
            raise
        except RuntimeError as exc:
            raise TurnFailedError("Codex SDK turn failed or completion event was missing") from exc
        if result.status != TurnStatus.completed:
            raise TurnFailedError(f"Codex turn ended with status {result.status.value}")
        if not result.final_response:
            raise TurnFailedError("Codex completed without a text response")
        return RuntimeResponse(
            result.final_response,
            thread.id,
            result.id,
            usage=usage_report(getattr(result, "usage", None)),
            metadata={
                "status": result.status.value,
                "duration_ms": getattr(result, "duration_ms", None),
                "started_at": getattr(result, "started_at", None),
                "completed_at": getattr(result, "completed_at", None),
                "requested_model": self.config.model,
                "configured_model": bridge.configured_model,
                "items": item_summaries(getattr(result, "items", [])),
            },
        )
