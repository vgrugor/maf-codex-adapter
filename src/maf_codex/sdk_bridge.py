"""Pinned SDK 0.162.1 seam for experimental tools and non-blocking server requests."""

import asyncio
import contextvars
import threading
from contextlib import suppress

from openai_codex import AsyncCodex
from openai_codex.client import CodexClient

from .tools import ToolBridge


class BridgeTransport(CodexClient):
    def __init__(self, config, bridge: ToolBridge):
        super().__init__(config)
        self.bridge = bridge
        self.loop = asyncio.get_running_loop()
        self.context = contextvars.copy_context()
        self.pending = set()
        self.pending_lock = threading.Lock()
        self.bootstraps = set()

    def _write_message(self, payload):
        if payload.get("method") in {"thread/start", "thread/resume"}:
            self.bootstraps.add(payload["id"])
            payload = dict(payload)
            params = dict(payload.get("params") or {})
            params["approvalPolicy"] = "on-request" if self.bridge.approval_handler else "never"
            params["approvalsReviewer"] = "user"
            if payload["method"] == "thread/start":
                params["dynamicTools"] = self.bridge.specs
            payload["params"] = params
        super()._write_message(payload)

    def _reply(self, request, future):
        with self.pending_lock:
            self.pending.discard(future)
        try:
            result = future.result()
            payload = {"id": request["id"], "result": result}
        except Exception:  # noqa: BLE001 - a failed/cancelled server request receives an RPC error
            payload = {
                "id": request["id"],
                "error": {"code": -32601, "message": "Request rejected or cancelled"},
            }
        with suppress(Exception):
            self._write_message(payload)

    def _dispatch(self, request):
        if self.bridge.closed:
            with suppress(Exception):
                self._write_message(
                    {"id": request["id"], "error": {"code": -32601, "message": "Client closed"}}
                )
            return
        coroutine = self.bridge.handle(
            request["method"], request.get("params"), rpc_request_id=request["id"]
        )
        future = self.context.copy().run(asyncio.run_coroutine_threadsafe, coroutine, self.loop)
        with self.pending_lock:
            self.pending.add(future)
        future.add_done_callback(lambda completed: self._reply(request, completed))

    def _reader_loop(self):
        # Keep SDK routing semantics, but don't block stdout on approval/tool awaits.
        try:
            while True:
                message = self._read_message()
                if "method" in message and "id" in message:
                    self._dispatch(message)
                elif "method" in message:
                    self.bridge.observe(message["method"], message.get("params"))
                    self._router.route_notification(
                        self._coerce_notification(message["method"], message.get("params"))
                    )
                else:
                    if message.get("id") in self.bootstraps:
                        self.bootstraps.discard(message["id"])
                        result = message.get("result")
                        thread = result.get("thread") if isinstance(result, dict) else None
                        model = thread.get("model") if isinstance(thread, dict) else None
                        self.bridge.configured_model = model if isinstance(model, str) else None
                    self._router.route_response(message)
        except BaseException as exc:  # noqa: BLE001 - mirrors SDK router shutdown on terminal reader failures
            self._router.fail_all(exc)

    async def stop_requests(self):
        self.bridge.closed = True
        self.bridge.ready.set()
        with self.pending_lock:
            pending = tuple(self.pending)
        for future in pending:
            future.cancel()
        await asyncio.gather(
            *(asyncio.wrap_future(future) for future in pending), return_exceptions=True
        )


class BridgedCodex(AsyncCodex):
    def __init__(self, config, bridge: ToolBridge):
        super().__init__(config)
        self._client._sync = BridgeTransport(config, bridge)

    async def close(self):
        await self._client._sync.stop_requests()
        await super().close()
