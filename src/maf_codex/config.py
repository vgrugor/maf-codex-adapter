"""Explicit configuration for local Codex execution."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openai_codex import Sandbox


@dataclass(frozen=True)
class CodexAgentConfig:
    cwd: Path
    instructions: str = ""
    model: str | None = None
    sandbox: Sandbox = Sandbox.read_only
    timeout_seconds: float = 120
    codex_bin: str | None = None
    history_mode: Literal["incremental", "full"] = "incremental"

    def __post_init__(self) -> None:
        path = self.cwd.expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError("cwd must be an existing directory")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.history_mode not in {"incremental", "full"}:
            raise ValueError("history_mode must be incremental or full")
        object.__setattr__(self, "cwd", path)

    @property
    def fingerprint(self) -> str:
        payload = [str(self.cwd), self.instructions, self.model, self.sandbox.value, self.codex_bin]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()
