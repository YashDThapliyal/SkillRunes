"""Abstract interface for transcript providers.

Each provider knows how to locate and parse session files for a particular
AI coding tool (Claude Code, Codex, etc.).  The CLI layer calls
find_new_sessions() from the __init__ re-export and never imports a
specific provider directly.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from skillrunes.models import RawMessage


class TranscriptProvider(ABC):
    """Abstract base for a coding-agent transcript provider."""

    name: str
    display_name: str

    @abstractmethod
    def find_session_files(self, cwd: Path) -> list[Path]:
        """Return all transcript files that belong to cwd."""
        ...

    @abstractmethod
    def parse_session_file(self, path: Path) -> tuple[list[RawMessage], list[dict]]:
        """Parse a transcript file into (messages, raw_lines)."""
        ...

    @abstractmethod
    def session_id_from_path(self, path: Path) -> str:
        """Extract the session identifier from a transcript file path."""
        ...

    def find_new_sessions(self, cwd: Path) -> list[Path]:
        """Return transcript files not yet present in .skillrunes/sessions/.

        Default implementation compares file session IDs against
        store.known_session_ids().  Override if the provider needs
        different incremental-detection logic.
        """
        from skillrunes.store import known_session_ids

        all_files = self.find_session_files(cwd)
        already_done = known_session_ids()
        return [p for p in all_files if self.session_id_from_path(p) not in already_done]

    def expected_location(self, cwd: Path) -> str:
        """Return a human-readable hint for where this provider stores transcripts."""
        return "the selected provider's transcript directory"


# Backward-compatible alias for existing imports/tests while the codebase moves
# to the provider-agnostic name.
SessionProvider = TranscriptProvider


class ProviderNotImplementedError(NotImplementedError):
    """Raised when a selected transcript provider is not implemented yet."""
