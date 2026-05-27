"""Codex session transcript provider.

Codex support is intentionally explicit rather than silently returning no
sessions.  Local Codex session directories exist on some machines, but the
stable JSONL transcript format is not implemented here yet.
"""

from pathlib import Path

from skillrunes.config import CODEX_SESSION_ROOTS
from skillrunes.ingest.base import ProviderNotImplementedError, TranscriptProvider
from skillrunes.models import RawMessage

CODEX_NOT_IMPLEMENTED_MESSAGE = (
    "Codex transcript ingestion is not implemented yet. "
    "Set SKILLRUNES_PROVIDER=claude_code or provide a Codex transcript path."
)


class CodexProvider(TranscriptProvider):
    """Session provider for OpenAI Codex transcripts (not yet implemented)."""

    name = "codex"
    display_name = "Codex"

    def find_session_files(self, cwd: Path) -> list[Path]:
        roots = [Path(root).expanduser() for root in CODEX_SESSION_ROOTS]
        existing_roots = [root for root in roots if root.exists()]
        if existing_roots:
            raise ProviderNotImplementedError(CODEX_NOT_IMPLEMENTED_MESSAGE)
        raise ProviderNotImplementedError(CODEX_NOT_IMPLEMENTED_MESSAGE)

    def parse_session_file(self, path: Path) -> tuple[list[RawMessage], list[dict]]:
        raise ProviderNotImplementedError(CODEX_NOT_IMPLEMENTED_MESSAGE)

    def session_id_from_path(self, path: Path) -> str:
        raise ProviderNotImplementedError(CODEX_NOT_IMPLEMENTED_MESSAGE)

    def expected_location(self, cwd: Path) -> str:
        return ", ".join(CODEX_SESSION_ROOTS)
