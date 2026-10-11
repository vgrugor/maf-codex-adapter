"""Explicit configuration for local Codex execution."""

import hashlib
import json
import math
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
        path = Path(self.cwd).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError("cwd must be an existing directory")
        if (
            type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if not isinstance(self.instructions, str):
            raise TypeError("instructions must be a string")
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()):
            raise ValueError("model must be a non-empty string or None")
        if not isinstance(self.sandbox, Sandbox):
            raise TypeError("sandbox must be a Sandbox enum")
        if self.codex_bin is not None and (
            not isinstance(self.codex_bin, str) or not self.codex_bin.strip()
        ):
            raise ValueError("codex_bin must be a non-empty string or None")
        if self.history_mode not in ("incremental", "full"):
            raise ValueError("history_mode must be incremental or full")
        object.__setattr__(self, "cwd", path)

    @property
    def fingerprint(self) -> str:
        payload = [str(self.cwd), self.instructions, self.model, self.sandbox.value, self.codex_bin]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()
