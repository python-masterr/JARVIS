from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    return Path(os.environ.get("JARVIS_DATA_DIR") or Path.home() / ".jarvis")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    host: str = "127.0.0.1"  # loopback only; never bind elsewhere
    port: int = 8750
    database_url: str | None = None
    claude_timeout_s: float = 60.0
    claude_max_retries: int = 4
    claude_backoff_base_s: float = 0.5
    claude_backoff_max_s: float = 20.0
    claude_max_tokens: int = 2048
    sonnet_model: str = field(default_factory=lambda: os.environ.get("JARVIS_SONNET_MODEL", "claude-sonnet-5-5"))
    opus_model: str = field(default_factory=lambda: os.environ.get("JARVIS_OPUS_MODEL", "claude-opus-5-5"))

    def resolved_database_url(self) -> str:
        return (
            self.database_url
            or os.environ.get("JARVIS_DATABASE_URL")
            or f"sqlite:///{(self.data_dir / 'jarvis.db').as_posix()}"
        )

    def model_for(self, alias: str) -> str:
        return {"sonnet": self.sonnet_model, "opus": self.opus_model}[alias]

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            os.chmod(self.data_dir, 0o700)
