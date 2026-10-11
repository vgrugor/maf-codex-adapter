"""Public adapter errors."""


class CodexAdapterError(RuntimeError):
    """A Codex-backed agent could not complete its request."""

    category = "adapter"
    transient = False


class TransportError(CodexAdapterError):
    """SDK process/transport failed; original cause remains chained."""

    category = "transport"


class ProtocolError(CodexAdapterError):
    """SDK rejected or could not decode a protocol request/response."""

    category = "protocol"


class ServiceBusyError(CodexAdapterError):
    """Transient server condition, without implying a side-effect-safe retry."""

    category = "service_busy"
    transient = True


class ThreadBusyError(CodexAdapterError):
    """Another run in this event loop owns the requested Codex thread."""

    category = "thread_busy"
    transient = True


class AuthenticationError(CodexAdapterError):
    """ChatGPT authentication is missing or API credentials are configured."""

    category = "authentication"


class SessionBindingError(CodexAdapterError):
    """A stored session binding is invalid or belongs to different configuration."""

    category = "session"


class TurnFailedError(CodexAdapterError):
    """Codex did not complete the requested turn."""

    category = "turn"


class HistoryMismatchError(SessionBindingError):
    """Full history diverges from acknowledged context or its state is uncertain."""
