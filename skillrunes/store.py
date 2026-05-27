"""Persistent storage layer for SkillRunes.

This module is the sole owner of all .skillrunes/ directory I/O.
No other module in the package may read from or write to .skillrunes/ directly.

Directory layout managed here:
    .skillrunes/
    ├── sessions/       Per-session files:
    │   ├── <session_id>.json       Extracted SessionSummary
    │   └── <session_id>.raw.json   Raw JSONL lines (safety archive)
    ├── skills/         Archived SKILL.md versions (v1.md, v2.md, ...)
    └── metrics.json    Aggregated ProjectMetrics
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from skillrunes.config import STORE_DIR_NAME
from skillrunes.models import ProjectMetrics, SessionSummary, SkillVersion
from skillrunes.utils import now_utc


def _store_root() -> Path:
    """Return the absolute path to the .skillrunes/ directory for the cwd."""
    return Path.cwd() / STORE_DIR_NAME


def _sessions_dir() -> Path:
    return _store_root() / "sessions"


def _skills_dir() -> Path:
    return _store_root() / "skills"


def _metrics_path() -> Path:
    return _store_root() / "metrics.json"


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_store() -> None:
    """Create the .skillrunes/ folder structure if it does not exist."""
    _sessions_dir().mkdir(parents=True, exist_ok=True)
    _skills_dir().mkdir(parents=True, exist_ok=True)
    if not _metrics_path().exists():
        _atomic_write(_metrics_path(), ProjectMetrics().model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# Session summaries
# ---------------------------------------------------------------------------

def save_session_summary(summary: SessionSummary) -> None:
    """Persist a SessionSummary as <session_id>.json inside .skillrunes/sessions/."""
    path = _sessions_dir() / f"{summary.session_id}.json"
    _atomic_write(path, summary.model_dump_json(indent=2))


def load_all_summaries() -> list[SessionSummary]:
    """Load every persisted SessionSummary from .skillrunes/sessions/."""
    summaries: list[SessionSummary] = []
    sessions_dir = _sessions_dir()
    if not sessions_dir.exists():
        return summaries
    # Match only extracted summaries, not raw archives (*.raw.json)
    for path in sorted(p for p in sessions_dir.glob("*.json") if ".raw." not in p.name):
        try:
            summaries.append(SessionSummary.model_validate_json(path.read_text()))
        except Exception:
            pass  # Skip corrupted summary files silently
    return summaries


def known_session_ids() -> set[str]:
    """Return the set of session IDs that have already been analysed.

    Matches only extracted summaries (<session_id>.json), not raw archives
    (<session_id>.raw.json), so the two file types don't collide.
    """
    sessions_dir = _sessions_dir()
    if not sessions_dir.exists():
        return set()
    return {
        p.stem
        for p in sessions_dir.glob("*.json")
        if ".raw." not in p.name
    }


def archive_raw_session(session_id: str, raw_lines: list[dict]) -> None:
    """Save raw JSONL lines to .skillrunes/sessions/<session_id>.raw.json.

    This is a safety net against providers that rotate/delete transcripts.
    The raw archive is written before any model extraction so that the
    original transcript is preserved even if extraction fails or the source
    file is later removed.
    """
    import json

    path = _sessions_dir() / f"{session_id}.raw.json"
    _atomic_write(path, json.dumps(raw_lines, indent=2, default=str))


# ---------------------------------------------------------------------------
# Skill versioning
# ---------------------------------------------------------------------------

def _next_version_number() -> int:
    """Determine the next skill version number from existing archived files."""
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return 1
    existing = [
        int(p.stem[1:])  # strip leading 'v'
        for p in skills_dir.glob("v*.md")
        if p.stem[1:].isdigit()
    ]
    return max(existing, default=0) + 1


def save_skill_version(content: str, change_summary: str = "") -> SkillVersion:
    """Archive the new skill content and write SKILL.md to the project root.

    IMPORTANT: This function always archives the new content to
    .skillrunes/skills/v{n}.md BEFORE writing SKILL.md, so the full version
    history is preserved even if the write is interrupted.  Never call
    SKILL.md writes from anywhere else.
    """
    version = _next_version_number()
    skill_version = SkillVersion(
        version=version,
        timestamp=now_utc(),
        content=content,
        change_summary=change_summary,
    )

    # 1. Archive to version history first
    archive_path = _skills_dir() / f"v{version}.md"
    _atomic_write(archive_path, content)

    # 2. Write the canonical SKILL.md at the project root
    skill_path = Path.cwd() / "SKILL.md"
    _atomic_write(skill_path, content)

    return skill_version


def load_current_skill() -> SkillVersion | None:
    """Return the most recent SkillVersion, or None if no versions exist."""
    history = load_skill_history()
    return history[-1] if history else None


def load_skill_history() -> list[SkillVersion]:
    """Return all archived skill versions sorted oldest-first."""
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return []

    versions: list[SkillVersion] = []
    for path in sorted(skills_dir.glob("v*.md"), key=lambda p: int(p.stem[1:])):
        if not path.stem[1:].isdigit():
            continue
        version_num = int(path.stem[1:])
        versions.append(
            SkillVersion(
                version=version_num,
                timestamp=_mtime(path),
                content=path.read_text(),
                change_summary="",
            )
        )
    return versions


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def save_metrics(metrics: ProjectMetrics) -> None:
    """Persist aggregated ProjectMetrics to .skillrunes/metrics.json."""
    _atomic_write(_metrics_path(), metrics.model_dump_json(indent=2))


def load_metrics() -> ProjectMetrics | None:
    """Load ProjectMetrics from .skillrunes/metrics.json, or None if absent."""
    path = _metrics_path()
    if not path.exists():
        return None
    try:
        return ProjectMetrics.model_validate_json(path.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically using a temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _mtime(path: Path) -> datetime:
    """Return the file modification time as a UTC datetime."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
