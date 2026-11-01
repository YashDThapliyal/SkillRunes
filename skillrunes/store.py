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
from skillrunes.models import (
    BenchReport,
    BenchRunResult,
    EvalResult,
    ImpactReport,
    ProjectMetrics,
    RuleBook,
    SessionSummary,
    SkillExport,
    SkillVersion,
    TraceExport,
)
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


def _exports_dir() -> Path:
    return _store_root() / "exports"


def _evals_dir() -> Path:
    return _store_root() / "evals"


def _traces_dir() -> Path:
    return _store_root() / "traces"


def _impact_path() -> Path:
    return _store_root() / "impact.json"


def _rulebook_path() -> Path:
    return _store_root() / "rules.json"


def bench_dir(suite_name: str) -> Path:
    """Default output directory for a benchmark suite's runs."""
    return _store_root() / "bench" / suite_name


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_store() -> None:
    """Create the .skillrunes/ folder structure if it does not exist."""
    _sessions_dir().mkdir(parents=True, exist_ok=True)
    _skills_dir().mkdir(parents=True, exist_ok=True)
    _exports_dir().mkdir(parents=True, exist_ok=True)
    _evals_dir().mkdir(parents=True, exist_ok=True)
    _traces_dir().mkdir(parents=True, exist_ok=True)
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


def load_raw_session(session_id: str) -> list[dict] | None:
    """Return archived raw lines for session_id, or None if not archived."""
    import json

    path = _sessions_dir() / f"{session_id}.raw.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, list) else None


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


def save_skill_version(
    content: str,
    change_summary: str = "",
    *,
    token_estimate: int = 0,
    compression_report=None,
    reference_content: str = "",
) -> SkillVersion:
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
        token_estimate=token_estimate,
        compression_report=compression_report,
        reference_content=reference_content,
    )

    # 1. Archive to version history first
    archive_path = _skills_dir() / f"v{version}.md"
    _atomic_write(archive_path, content)
    _atomic_write(
        _skills_dir() / f"v{version}.json",
        skill_version.model_dump_json(indent=2),
    )

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
    numbered = [p for p in skills_dir.glob("v*.md") if p.stem[1:].isdigit()]
    for path in sorted(numbered, key=lambda p: int(p.stem[1:])):
        version_num = int(path.stem[1:])
        metadata_path = _skills_dir() / f"v{version_num}.json"
        if metadata_path.exists():
            try:
                versions.append(SkillVersion.model_validate_json(metadata_path.read_text()))
                continue
            except Exception:
                pass
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
# Exports, evals, and traces
# ---------------------------------------------------------------------------

def save_skill_exports(exports: list[SkillExport]) -> None:
    """Persist export metadata."""
    if not exports:
        return
    stamp = now_utc().strftime("%Y%m%d%H%M%S")
    path = _exports_dir() / f"exports-{stamp}.json"
    _atomic_write(path, "[" + ",".join(e.model_dump_json() for e in exports) + "]")


def write_project_file(path: Path, content: str) -> None:
    """Write a project file atomically."""
    _atomic_write(Path.cwd() / path, content)


def read_project_file(path: Path) -> str:
    """Read a project file, returning an empty string if absent."""
    full_path = Path.cwd() / path
    if not full_path.exists():
        return ""
    return full_path.read_text(encoding="utf-8")


def save_impact_report(report: ImpactReport) -> Path:
    """Persist the latest skill impact report to .skillrunes/impact.json."""
    path = _impact_path()
    _atomic_write(path, report.model_dump_json(indent=2))
    return path


def load_impact_report() -> ImpactReport | None:
    """Return the saved impact report, or None if absent or unreadable."""
    path = _impact_path()
    if not path.exists():
        return None
    try:
        return ImpactReport.model_validate_json(path.read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Rule-level memory
# ---------------------------------------------------------------------------

def load_rulebook() -> RuleBook:
    """Return the saved rulebook, or an empty one."""
    path = _rulebook_path()
    if not path.exists():
        return RuleBook()
    try:
        return RuleBook.model_validate_json(path.read_text())
    except (OSError, ValueError):
        return RuleBook()


def save_rulebook(book: RuleBook) -> Path:
    """Persist the rulebook to .skillrunes/rules.json."""
    path = _rulebook_path()
    _atomic_write(path, book.model_dump_json(indent=2))
    return path


# ---------------------------------------------------------------------------
# Benchmark runs (directory may live outside .skillrunes/ when --out is given)
# ---------------------------------------------------------------------------

BENCH_RESULTS_FILE = "results.jsonl"


def append_bench_result(run_dir: Path, result: BenchRunResult) -> None:
    """Append one run result to <run_dir>/results.jsonl."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / BENCH_RESULTS_FILE).open("a", encoding="utf-8") as fh:
        fh.write(result.model_dump_json() + "\n")


def load_bench_results(run_dir: Path) -> list[BenchRunResult]:
    """Load recorded run results, skipping unreadable lines."""
    path = run_dir / BENCH_RESULTS_FILE
    if not path.exists():
        return []
    results: list[BenchRunResult] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            results.append(BenchRunResult.model_validate_json(line))
        except ValueError:
            continue
    return results


def write_bench_transcript(run_dir: Path, name: str, lines: list[dict]) -> Path:
    """Write a run transcript as JSONL under <run_dir>/transcripts/."""
    import json

    path = run_dir / "transcripts" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, "".join(json.dumps(line, default=str) + "\n" for line in lines))
    return path


def save_bench_report(run_dir: Path, report: BenchReport) -> Path:
    """Persist an aggregated benchmark report next to its results."""
    path = run_dir / f"report-{report.split}.json"
    _atomic_write(path, report.model_dump_json(indent=2))
    return path


def write_text_file(path: Path, content: str) -> Path:
    """Write a generated instruction file (e.g. a learned skill) atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, content)
    return path


def save_eval_result(result: EvalResult) -> Path:
    """Persist one eval result JSON artifact."""
    path = _evals_dir() / (
        f"eval-v{result.before_version}-v{result.after_version}-"
        f"{result.generated_at.strftime('%Y%m%d%H%M%S')}.json"
    )
    _atomic_write(path, result.model_dump_json(indent=2))
    return path


def save_trace_exports(exports: list[TraceExport]) -> Path:
    """Persist trace exports as one JSON artifact."""
    stamp = now_utc().strftime("%Y%m%d%H%M%S")
    path = _traces_dir() / f"otel-traces-{stamp}.json"
    _atomic_write(path, "[" + ",".join(e.model_dump_json() for e in exports) + "]")
    return path


def privacy_scan_paths() -> list[Path]:
    """Return local files doctor privacy should scan."""
    root = Path.cwd()
    paths: list[Path] = []
    for candidate in (root / "SKILL.md", root / "AGENTS.md"):
        if candidate.exists():
            paths.append(candidate)
    for folder in (_sessions_dir(), _exports_dir(), _skills_dir()):
        if folder.exists():
            paths.extend(p for p in folder.rglob("*") if p.is_file())
    claude_skill = root / ".claude" / "skills" / "skillrunes-project-memory"
    if claude_skill.exists():
        paths.extend(p for p in claude_skill.rglob("*") if p.is_file())
    return paths


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
