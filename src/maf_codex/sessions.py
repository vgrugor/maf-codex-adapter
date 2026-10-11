"""Versioned bindings and explicit full-history reconciliation."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from weakref import WeakKeyDictionary, WeakValueDictionary

from .errors import HistoryMismatchError, SessionBindingError, ThreadBusyError

# Share locks between agent instances in the same loop, without retaining sessions.
_locks = WeakKeyDictionary()
_thread_locks = WeakKeyDictionary()


def session_lock(agent_id: str, session_id: str) -> asyncio.Lock:
    registry = _locks.setdefault(asyncio.get_running_loop(), WeakValueDictionary())
    return registry.setdefault((agent_id, session_id), asyncio.Lock())


@asynccontextmanager
async def thread_lease(thread_id: str | None):
    if thread_id is None:
        yield
        return
    registry = _thread_locks.setdefault(asyncio.get_running_loop(), WeakValueDictionary())
    lock = registry.setdefault(thread_id, asyncio.Lock())
    if lock.locked():
        raise ThreadBusyError("Codex thread already has an active run")
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()


def validate_binding(binding: dict | None, fingerprint: str, history_mode: str) -> None:
    if binding is None:
        return
    if (
        not isinstance(binding, dict)
        or binding.get("fingerprint") != fingerprint
        or not isinstance(binding.get("thread_id"), str)
        or not binding["thread_id"]
        or type(binding.get("version", 0)) is not int
        or binding.get("version", 0) not in (0, 1)
        or binding.get("history_mode", "incremental") != history_mode
    ):
        raise SessionBindingError("Session binding does not match this agent configuration")


def new_records(incoming: list[dict], binding: dict | None, history_mode: str) -> list[dict]:
    if history_mode == "incremental" or binding is None:
        return incoming
    if binding.get("pending"):
        raise HistoryMismatchError(
            "Previous turn has uncertain history; full-history continuation is blocked"
        )
    history = binding.get("history")
    if not isinstance(history, list) or any(
        not isinstance(item, dict)
        or set(item) != {"role", "author", "text"}
        or item["role"] not in ("user", "assistant")
        or not isinstance(item["text"], str)
        or (item["author"] is not None and not isinstance(item["author"], str))
        for item in history
    ):
        raise HistoryMismatchError("Full-history binding has invalid acknowledged history")
    if incoming[: len(history)] != history:
        raise HistoryMismatchError("Full history must extend the exact acknowledged conversation")
    delta = incoming[len(history) :]
    if not delta:
        raise HistoryMismatchError("Full history contains no new messages")
    return delta


def bind(thread_id: str, fingerprint: str, history_mode: str, previous: dict | None) -> dict:
    return {
        "version": 1,
        "thread_id": thread_id,
        "fingerprint": fingerprint,
        "history_mode": history_mode,
        **(
            {"history": deepcopy(previous.get("history", [])) if previous else [], "pending": True}
            if history_mode == "full"
            else {}
        ),
    }
