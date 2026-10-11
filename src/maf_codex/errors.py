"""Public adapter errors."""


class CodexAdapterError(RuntimeError):
    """A Codex-backed agent could not complete its request."""


class AuthenticationError(CodexAdapterError):
    """ChatGPT authentication is missing or API credentials are configured."""


class SessionBindingError(CodexAdapterError):
    """A stored session binding is invalid or belongs to different configuration."""


class TurnFailedError(CodexAdapterError):
    """Codex did not complete the requested turn."""


class HistoryMismatchError(SessionBindingError):
    """Full history diverges from acknowledged context or its state is uncertain."""
