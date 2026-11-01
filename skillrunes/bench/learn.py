"""Produce instruction files for benchmark arms.

learn_skill():          the SkillRunes pipeline (extract → synthesise) applied
                        to train-split transcripts recorded by `bench run`.
generate_oneshot():     the common baseline: ask the agent once to write a
                        CLAUDE.md for the repo, like Claude Code's /init.

Model calls happen only inside extract.analyze_session and
synthesize.generate_skill_file (project rule).  The one-shot baseline is an
agent run, executed through the benchmark runner like every other run.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skillrunes.bench.runner import INSTRUCTION_FILE, prepare_workspace, run_agent
from skillrunes.compress import build_ranked_memory_seed, compress_skill_content
from skillrunes.extract import analyze_session
from skillrunes.ingest.claude_code import ClaudeCodeProvider
from skillrunes.models import BenchArm, BenchRunResult, BenchSuite, SessionSummary
from skillrunes.redact import redact_messages_and_raw
from skillrunes.synthesize import generate_skill_file

ONESHOT_PROMPT = (
    "Please analyze this codebase and create a CLAUDE.md file in the repository "
    "root. It will be given to future coding agents working in this repo. "
    "Include the commands needed to work here and the project's conventions "
    "and patterns that a contributor must follow. Keep it concise."
)
_BILLING = "subscription"


def learn_skill(
    results: list[BenchRunResult],
    *,
    budget_tokens: int | None = None,
    workers: int = 4,
) -> tuple[str, list[SessionSummary]]:
    """Run SkillRunes extraction + synthesis over train-split transcripts."""
    train = [r for r in results if r.split == "train" and r.transcript_path]
    if not train:
        raise ValueError("No train-split transcripts found; run `bench run --split train` first.")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        summaries = [s for s in pool.map(_summarise_run, train) if s is not None]
    if not summaries:
        raise ValueError("No train transcripts produced a summary.")
    seed = build_ranked_memory_seed(summaries, "balanced")
    content, _ = generate_skill_file(summaries, None, seed, billing=_BILLING)
    content, _, _ = compress_skill_content(content, budget_tokens=budget_tokens, mode="balanced")
    return content, summaries


def _summarise_run(result: BenchRunResult) -> SessionSummary | None:
    lines = _read_jsonl(Path(result.transcript_path))
    messages = ClaudeCodeProvider().messages_from_raw(lines)
    if not messages:
        return None
    messages, lines, _ = redact_messages_and_raw(messages, lines, "basic", Path.cwd())
    session_id = f"bench-{result.task_id}-{result.arm}-{result.seed}"
    return analyze_session(messages, session_id, lines, billing=_BILLING)


def generate_oneshot(suite: BenchSuite, model: str) -> str:
    """Ask the agent to write CLAUDE.md for the pristine project; return it."""
    with tempfile.TemporaryDirectory(prefix="skillrunes-oneshot-") as scratch:
        workspace = prepare_workspace(suite, BenchArm(name="none"), Path(scratch))
        stats, _ = run_agent(workspace, ONESHOT_PROMPT, model, suite.timeout_seconds)
        path = workspace / INSTRUCTION_FILE
        if not path.exists():
            raise RuntimeError(f"Agent did not write {INSTRUCTION_FILE} ({stats.error or 'no error'}).")
        content = path.read_text()
        shutil.rmtree(workspace, ignore_errors=True)
    return content


def _read_jsonl(path: Path) -> list[dict]:
    lines: list[dict] = []
    try:
        for raw in path.read_text().splitlines():
            if raw.strip():
                data = json.loads(raw)
                if isinstance(data, dict):
                    lines.append(data)
    except (OSError, json.JSONDecodeError):
        return lines
    return lines
