"""Transcript ingestion package for SkillRunes.

Public surface:

    find_session_files(cwd)   → list[Path]
    parse_session_file(path)  → tuple[list[RawMessage], list[dict]]
    session_id_from_path(path)→ str
    find_new_sessions(cwd)    → list[Path]

The active provider defaults to Claude Code and can be selected with
SKILLRUNES_PROVIDER or the CLI --provider option. AGENTLENS_PROVIDER is read
only as a backward-compatible fallback when SKILLRUNES_PROVIDER is unset.
"""

import os
from pathlib import Path

from skillrunes.config import (
    DEFAULT_PROVIDER,
    LEGACY_PROVIDER_ENV_VAR,
    PROVIDER_ENV_VAR,
    SUPPORTED_PROVIDERS,
)
from skillrunes.ingest.base import TranscriptProvider
from skillrunes.ingest.claude_code import ClaudeCodeProvider
from skillrunes.ingest.codex import CodexProvider
from skillrunes.models import RawMessage

_PROVIDERS: dict[str, type[TranscriptProvider]] = {
    "claude_code": ClaudeCodeProvider,
    "codex": CodexProvider,
}


def resolve_provider_name(provider_name: str | None = None) -> str:
    """Resolve and validate the requested transcript provider name."""
    selected = (
        provider_name
        or os.environ.get(PROVIDER_ENV_VAR)
        or os.environ.get(LEGACY_PROVIDER_ENV_VAR)
        or DEFAULT_PROVIDER
    ).strip()
    if selected not in SUPPORTED_PROVIDERS:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(
            f"Unknown SkillRunes provider {selected!r}. Supported providers: {supported}."
        )
    return selected


def get_provider(provider_name: str | None = None) -> TranscriptProvider:
    """Return a fresh provider instance for the selected provider name."""
    selected = resolve_provider_name(provider_name)
    return _PROVIDERS[selected]()


def find_session_files(cwd: Path, provider: TranscriptProvider | None = None) -> list[Path]:
    """Locate all session transcript files for the given project directory."""
    active_provider = provider or get_provider()
    return active_provider.find_session_files(cwd)


def parse_session_file(
    path: Path, provider: TranscriptProvider | None = None
) -> tuple[list[RawMessage], list[dict]]:
    """Parse a transcript file into (messages, raw_lines)."""
    active_provider = provider or get_provider()
    return active_provider.parse_session_file(path)


def session_id_from_path(path: Path, provider: TranscriptProvider | None = None) -> str:
    """Extract the session identifier from a transcript file path."""
    active_provider = provider or get_provider()
    return active_provider.session_id_from_path(path)


def find_new_sessions(cwd: Path, provider: TranscriptProvider | None = None) -> list[Path]:
    """Return session files not yet analysed for the given project directory."""
    active_provider = provider or get_provider()
    return active_provider.find_new_sessions(cwd)
