"""Claude Code session transcript provider.

Implements SessionProvider for Claude Code's JSONL transcript format stored
under ~/.claude/projects/.

Session transcript layout (legacy, Claude Code <1.x):
  ~/.claude/projects/<sanitized-cwd>/
      <session-uuid>.jsonl          ← main session transcript (what we read)

Session transcript layout (newer, Claude Code 1.x+):
  ~/.claude/projects/<sanitized-cwd>/
      sessions-index.json           ← index with fullPath to each JSONL
      <session-uuid>/
          subagents/
              agent-<id>.jsonl      ← subagent transcripts (excluded)
              agent-<id>.meta.json  ← subagent metadata (excluded)

The sessions-index.json file contains an "entries" array where each entry has
a "fullPath" pointing to the actual JSONL transcript, which may live anywhere
on disk (often inside ~/.claude/projects/<proj>/<uuid>/subagents/ or the parent
dir itself).  fullPath entries that don't exist on disk are skipped gracefully.
"""

import json
from pathlib import Path

from rich.console import Console

from skillrunes.config import CLAUDE_PROJECTS_ROOT
from skillrunes.ingest.base import TranscriptProvider
from skillrunes.models import RawMessage
from skillrunes.utils import sanitize_path_to_dirname

console = Console(stderr=True)

# Maximum number of lines to scan when searching for a cwd field to verify
# the project match. Most files have cwd on the first user/assistant line.
_CWD_SCAN_LIMIT = 30


class ClaudeCodeProvider(TranscriptProvider):
    """Session provider for Claude Code JSONL transcripts."""

    name = "claude_code"
    display_name = "Claude Code"

    def find_session_files(self, cwd: Path) -> list[Path]:
        """Locate all JSONL session files belonging to the given project directory.

        Strategy:
        1. Use the sanitized folder name as a fast pre-filter.
        2. Legacy format: glob top-level *.jsonl and verify cwd field.
        3. Newer format: read sessions-index.json and resolve fullPath entries.
        4. Deduplicate and return sorted by file modification time, oldest first.

        Returns paths sorted by file modification time, oldest first.
        """
        projects_root = Path(CLAUDE_PROJECTS_ROOT).expanduser()
        if not projects_root.exists():
            return []

        sanitized = sanitize_path_to_dirname(cwd)
        cwd_str = str(cwd)

        seen: set[Path] = set()
        candidates: list[Path] = []

        for project_dir in projects_root.iterdir():
            if not project_dir.is_dir():
                continue
            if project_dir.name != sanitized:
                continue

            # Legacy format: top-level *.jsonl files
            for jsonl_path in project_dir.glob("*.jsonl"):
                if jsonl_path not in seen and _verify_cwd_match(jsonl_path, cwd_str):
                    seen.add(jsonl_path)
                    candidates.append(jsonl_path)

            # Newer format: sessions-index.json (some Claude Code versions)
            index_path = project_dir / "sessions-index.json"
            if index_path.exists():
                for jsonl_path in _read_sessions_index(index_path, cwd_str):
                    if jsonl_path not in seen:
                        seen.add(jsonl_path)
                        candidates.append(jsonl_path)
            else:
                # No index — scan UUID subdirs directly for subagent JSONL files
                for jsonl_path in _scan_subagent_dirs(project_dir):
                    if jsonl_path not in seen:
                        seen.add(jsonl_path)
                        candidates.append(jsonl_path)

        return sorted(candidates, key=lambda p: p.stat().st_mtime)

    def parse_session_file(self, path: Path) -> tuple[list[RawMessage], list[dict]]:
        """Parse a single JSONL transcript file.

        Returns (messages, raw_lines):
          messages   — RawMessage objects for user/assistant lines only
          raw_lines  — every successfully parsed dict, for archive_raw_session

        Noise line types are excluded from messages but included in raw_lines.
        Malformed JSON lines are Rich-warned and skipped.
        """
        messages: list[RawMessage] = []
        raw_lines: list[dict] = []

        try:
            with path.open(encoding="utf-8") as fh:
                for lineno, raw_line in enumerate(fh, start=1):
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        data = json.loads(raw_line)
                    except json.JSONDecodeError as exc:
                        console.print(
                            f"[yellow]Warning:[/yellow] skipping malformed JSON on "
                            f"{path.name}:{lineno} — {exc}"
                        )
                        continue

                    raw_lines.append(data)
                    msg = RawMessage.from_jsonl_line(data)
                    if msg is not None:
                        messages.append(msg)

        except OSError as exc:
            console.print(f"[red]Error:[/red] could not read {path} — {exc}")

        return messages, raw_lines

    def session_id_from_path(self, path: Path) -> str:
        """Extract the session UUID from a JSONL file path (the file stem)."""
        return path.stem

    def expected_location(self, cwd: Path) -> str:
        """Return the Claude Code transcript location hint for cwd."""
        return f"~/.claude/projects/{sanitize_path_to_dirname(cwd)}"


# ---------------------------------------------------------------------------
# Module-level helpers (not part of the public provider interface)
# ---------------------------------------------------------------------------

def _verify_cwd_match(path: Path, cwd_str: str) -> bool:
    """Return True if the JSONL file's cwd field matches cwd_str."""
    try:
        with path.open(encoding="utf-8") as fh:
            for i, raw_line in enumerate(fh):
                if i >= _CWD_SCAN_LIMIT:
                    break
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    data = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                file_cwd = data.get("cwd", "")
                if file_cwd:
                    return file_cwd == cwd_str
    except OSError:
        pass
    return False


def _scan_subagent_dirs(project_dir: Path) -> list[Path]:
    """Return all subagent JSONL files in UUID subdirectories of project_dir.

    Used when no sessions-index.json is present but per-session UUID dirs exist.
    """
    results: list[Path] = []
    for child in project_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name in {"memory", ".git"}:
            continue
        subagents_dir = child / "subagents"
        if subagents_dir.is_dir():
            results.extend(sorted(subagents_dir.glob("*.jsonl")))
    return results


def _read_sessions_index(index_path: Path, cwd_str: str) -> list[Path]:
    """Parse sessions-index.json and return existing JSONL paths for cwd_str.

    For each entry:
    - If fullPath exists on disk, use it directly (hybrid format).
    - Otherwise fall back to <project_dir>/<sessionId>/subagents/*.jsonl
      (newer format where top-level JSONL is never written to disk).

    Entries are filtered by projectPath == cwd_str when that field is present.
    """
    try:
        with index_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[yellow]Warning:[/yellow] could not read {index_path} — {exc}")
        return []

    entries = data if isinstance(data, list) else data.get("entries", [])
    project_dir = index_path.parent
    results: list[Path] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        project_path = entry.get("projectPath", "")
        if project_path and project_path != cwd_str:
            continue

        full_path = entry.get("fullPath", "")
        if full_path:
            p = Path(full_path)
            if p.exists() and p.suffix == ".jsonl":
                results.append(p)
                continue

        # fullPath missing or not on disk — look in the subagents directory
        session_id = entry.get("sessionId", "")
        if not session_id:
            continue
        subagents_dir = project_dir / session_id / "subagents"
        if subagents_dir.is_dir():
            results.extend(sorted(subagents_dir.glob("*.jsonl")))

    return results
