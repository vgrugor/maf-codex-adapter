"""MAF FunctionTool execution and in-process approval policy for one Codex turn."""

import asyncio
import copy
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from agent_framework import AgentSession, FunctionInvocationContext, FunctionTool


@dataclass(frozen=True)
class ApprovalRequest:
    kind: Literal["tool", "command", "file"]
    session_id: str
    thread_id: str
    turn_id: str
    request_id: str
    name: str
    arguments: dict


ApprovalHandler = Callable[[ApprovalRequest], Awaitable[Literal["approve", "reject", "cancel"]]]


def validate_tools(tools: Sequence[FunctionTool] | None) -> list[FunctionTool]:
    result = list(tools or [])
    names = set()
    for tool in result:
        if not isinstance(tool, FunctionTool) or tool.declaration_only:
            raise TypeError("Tools must be callable MAF FunctionTool objects")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", tool.name) or tool.name in names:
            raise ValueError(
                "Tool names must be unique and contain 1..60 letters/digits/underscores/hyphens"
            )
        if tool.approval_mode not in {"always_require", "never_require"}:
            raise ValueError("Unsupported MAF tool approval mode")
        names.add(tool.name)
    return result


def tool_fingerprint(tools: Sequence[FunctionTool]) -> str:
    if not tools:
        return ""
    catalog = sorted(
        [(tool.to_json_schema_spec(), tool.approval_mode) for tool in tools],
        key=lambda item: item[0]["function"]["name"],
    )
    return hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest()


class ToolBridge:
    """No persistent grants; only the active thread/turn may invoke this registry."""

    def __init__(
        self,
        tools: Sequence[FunctionTool],
        session: AgentSession,
        approval_handler: ApprovalHandler | None = None,
    ):
        self.tools = {f"maf_{tool.name}": tool for tool in validate_tools(tools)}
        self.approval_modes = {name: tool.approval_mode for name, tool in self.tools.items()}
        self.session = session
        self.approval_handler = approval_handler
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.ready = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.closed = False
        self.seen: set[tuple[str, str]] = set()
        self.items: dict[tuple[str, str, str], dict] = {}
        self.configured_model: str | None = None
        self.expected_model: str | None = None

    @classmethod
    def empty(cls):
        return cls([], AgentSession())

    async def __aenter__(self):
        if self.closed:
            raise RuntimeError("ToolBridge cannot be reused after its run")
        return self

    async def __aexit__(self, *args):
        self.closed = True
        self.ready.set()

    @property
    def specs(self) -> list[dict]:
        return [
            {
                "type": "function",
                "name": name,
                "description": tool.description,
                "inputSchema": tool.to_json_schema_spec()["function"]["parameters"],
                "deferLoading": False,
            }
            for name, tool in self.tools.items()
        ]

    def start_thread(self, thread_id: str) -> None:
        self.thread_id = thread_id

    def start_turn(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self.ready.set()

    def observe(self, method: str, params: dict | None):
        if method != "item/started" or not isinstance(params, dict):
            return
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") not in {"fileChange", "commandExecution"}:
            return
        key = (
            params.get("threadId"),
            params.get("turnId"),
            item.get("id") if isinstance(item, dict) else None,
        )
        if all(isinstance(value, str) and value for value in key):
            self.items[key] = copy.deepcopy(item)

    @staticmethod
    def failure(text: str) -> dict:
        return {"success": False, "contentItems": [{"type": "inputText", "text": text}]}

    async def decision(self, request: ApprovalRequest) -> str:
        if self.approval_handler is None:
            return "reject"
        try:
            decision = await self.approval_handler(request)
        except Exception:  # noqa: BLE001 - external callback/schema failures must fail closed
            return "reject"
        if decision not in ("approve", "reject", "cancel"):
            return "reject"
        if decision == "cancel":
            self.cancelled.set()
        return decision

    async def handle(self, method: str, params: dict | None, *, rpc_request_id=None) -> dict:
        params = copy.deepcopy(params or {})
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn"}
        if method == "mcpServer/elicitation/request":
            return {"action": "cancel", "content": None}
        native = method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }
        if method != "item/tool/call" and not native:
            raise NotImplementedError("Unsupported Codex server request")
        failure = {"decision": "decline"} if native else self.failure("Tool request rejected")
        if any(
            not isinstance(params.get(key), str) or not params[key]
            for key in ("threadId", "turnId")
        ):
            return failure
        if self.closed or self.cancelled.is_set() or params.get("threadId") != self.thread_id:
            return failure
        # Server requests may arrive before the turn/start reply reaches the facade.
        await self.ready.wait()
        if self.closed or self.cancelled.is_set() or params.get("turnId") != self.turn_id:
            return failure
        request_id = params.get("itemId") if native else params.get("callId")
        if not isinstance(request_id, str) or not request_id:
            return failure
        # One native item may request multiple approvals for distinct network hosts.
        native_request_id = (
            str(rpc_request_id) if native and rpc_request_id is not None else request_id
        )
        key = (method, native_request_id if native else request_id)
        if key in self.seen:
            return failure
        self.seen.add(key)
        if native:
            kind = "command" if method == "item/commandExecution/requestApproval" else "file"
            preview = copy.deepcopy(params)
            preview["item"] = copy.deepcopy(
                self.items.get((self.thread_id, self.turn_id, request_id))
            )
            request = ApprovalRequest(
                kind,
                self.session.session_id,
                self.thread_id,
                self.turn_id,
                native_request_id,
                params.get("command") or kind,
                preview,
            )
            decision = await self.decision(request)
            if (
                self.closed
                or params.get("turnId") != self.turn_id
                or params.get("threadId") != self.thread_id
            ):
                return failure
            wire = {"approve": "accept", "reject": "decline", "cancel": "cancel"}[decision]
            available = params.get("availableDecisions")
            if available is not None and (not isinstance(available, list) or wire not in available):
                wire = "decline"
            return {"decision": wire}

        tool_name = params.get("tool")
        tool = self.tools.get(tool_name) if isinstance(tool_name, str) else None
        if tool is None or params.get("namespace") not in (None, ""):
            return self.failure("Unknown tool")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            return self.failure("Invalid tool arguments")
        try:
            if set(arguments) - set(
                tool.to_json_schema_spec()["function"]["parameters"].get("properties", {})
            ):
                return self.failure("Unexpected tool arguments")
            context = FunctionInvocationContext(
                function=tool,
                arguments=arguments,
                session=self.session,
                parent_service_session_state_keys={"maf_codex"},
                metadata={
                    "codex_thread_id": self.thread_id,
                    "codex_turn_id": self.turn_id,
                    "codex_call_id": request_id,
                },
            )
            # Pinned MAF seam: reuse its argument snapshot, including JSON-schema as_tool().
            parsed = tool._prepare_context_arguments(context, arguments)
        except Exception:  # noqa: BLE001 - external callback/schema failures must fail closed
            return self.failure("Invalid tool arguments")
        if self.approval_modes[tool_name] == "always_require":
            request = ApprovalRequest(
                "tool",
                self.session.session_id,
                self.thread_id,
                self.turn_id,
                request_id,
                tool.name,
                copy.deepcopy(parsed),
            )
            if await self.decision(request) != "approve":
                return self.failure("Tool approval rejected or cancelled")
        if (
            self.closed
            or self.cancelled.is_set()
            or params.get("turnId") != self.turn_id
            or params.get("threadId") != self.thread_id
        ):
            return self.failure("Tool invocation cancelled")
        try:
            result = await tool.invoke(arguments=parsed, context=context, tool_call_id=request_id)
            if not isinstance(result, list) or any(item.type != "text" for item in result):
                return self.failure("Only text tool output is supported")
            return {
                "success": True,
                "contentItems": [{"type": "inputText", "text": item.text} for item in result],
            }
        except Exception as exc:  # noqa: BLE001 - tool exceptions are returned without sensitive details
            return self.failure(f"Tool execution failed ({type(exc).__name__})")

    async def collect(self, turn):
        running = asyncio.create_task(turn.run())
        cancel = asyncio.create_task(self.cancelled.wait())
        try:
            await asyncio.wait((running, cancel), return_when=asyncio.FIRST_COMPLETED)
            if self.cancelled.is_set():
                running.cancel()
                raise asyncio.CancelledError("Host cancelled the Codex turn")
            return await running
        finally:
            running.cancel()
            cancel.cancel()
            await asyncio.gather(running, cancel, return_exceptions=True)
