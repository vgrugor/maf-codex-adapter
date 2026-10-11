"""Pinned MAF provider seam; keep the private response setter in one place."""

from contextvars import ContextVar

from agent_framework import AgentResponse, HistoryProvider, SessionContext
from agent_framework._middleware import categorize_middleware

invocation: ContextVar[dict | None] = ContextVar("maf_codex_invocation", default=None)


def agent_middleware_only(items) -> list:
    copied = list(items or [])
    categories = categorize_middleware(copied)
    if categories["chat"] or categories["function"]:
        raise TypeError("Only agent-level middleware is supported")
    return copied


def validate_providers(items) -> list:
    copied = list(items or [])
    source_ids = [provider.source_id for provider in copied]
    if any(
        not isinstance(source, str) or not source or source == "maf_codex" for source in source_ids
    ):
        raise ValueError("Provider source ids must be non-empty and must not use maf_codex")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Provider source ids must be unique")
    if any(isinstance(provider, HistoryProvider) for provider in copied):
        raise TypeError("HistoryProvider is not supported; Codex owns conversation history")
    return copied


def set_response(context: SessionContext, response: AgentResponse) -> None:
    # MAF 1.21.0 exposes response read-only; its own Agent uses this backing field.
    context._response = response
