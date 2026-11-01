"""Execute benchmark runs: headless `claude -p` in throwaway workspaces.

Every agent subprocess runs with:
  * a scrubbed environment (billing.subscription_env) so it bills the logged-in
    Claude subscription, never an API key;
  * --setting-sources project and --strict-mcp-config, so the user's global
    rules, hooks, plugins and MCP servers cannot leak into the experiment;
  * a fresh copy of the suite project as cwd, with the arm's instruction file
    written to CLAUDE.md (absent for the "none" arm).

stream-json output lines share the shape of Claude Code transcript lines, so
the same parser and signal detectors used on real sessions apply here.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import skillrunes.store as store
from skillrunes.billing import subscription_env
from skillrunes.config import CLAUDE_PROJECTS_ROOT
from skillrunes.ingest.claude_code import ClaudeCodeProvider
from skillrunes.models import (
    AgentRunStats,
    BenchArm,
    BenchRunResult,
    BenchSuite,
    BenchTask,
)
from skillrunes.signals import compute_signals
from skillrunes.utils import now_utc

ALLOWED_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"
INSTRUCTION_FILE = "CLAUDE.md"
FEEDBACK_PREFIX = "A reviewer looked at your change:\n\n"
BENCH_DIR_MARKER = "skillrunes-bench-"
_IGNORED_COPY = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".venv")
_CHECK_TIMEOUT = 300


@dataclass(frozen=True)
class RunConfig:
    """Settings shared by every run in a benchmark invocation."""

    model: str
    out_dir: Path
    feedback_rounds: int = 0
    keep_workspaces: bool = False


# ---------------------------------------------------------------------------
# Single agent invocation
# ---------------------------------------------------------------------------

def agent_command(prompt: str, model: str, *, resume: str = "", persist: bool = False) -> list[str]:
    """Build the isolated headless claude command line."""
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--model", model,
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
    ]
    if resume:
        cmd += ["--resume", resume]
    if not persist:
        cmd.append("--no-session-persistence")
    return cmd


def agent_env() -> dict[str, str]:
    """Subscription-only env with the harness Python first on PATH.

    Putting the harness interpreter first means `python -m pytest` inside the
    workspace finds an interpreter that already has pytest installed.
    """
    env = subscription_env()
    python_bin = str(Path(sys.executable).parent)
    env["PATH"] = os.pathsep.join([python_bin, env.get("PATH", "")])
    return env


def run_agent(
    workspace: Path,
    prompt: str,
    model: str,
    timeout: int,
    *,
    resume: str = "",
    persist: bool = False,
) -> tuple[AgentRunStats, list[dict[str, Any]]]:
    """Run one headless agent turn and return parsed stats plus raw lines."""
    try:
        proc = subprocess.run(
            agent_command(prompt, model, resume=resume, persist=persist),
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=agent_env(),
            stdin=subprocess.DEVNULL,
        )
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code, timed_out = _decode(exc.stdout), "", -1, True
    lines = parse_stream(stdout)
    stats = stats_from_stream(lines)
    if timed_out:
        stats = stats.model_copy(update={"is_error": True, "error": f"agent timed out after {timeout}s"})
    elif code != 0:
        detail = stderr.strip()[-500:] or stats.error or f"exit code {code}"
        stats = stats.model_copy(update={"is_error": True, "error": detail})
    return stats, lines


def parse_stream(stdout: str) -> list[dict[str, Any]]:
    """Parse stream-json stdout into dicts, skipping non-JSON noise."""
    lines: list[dict[str, Any]] = []
    for raw in stdout.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            lines.append(data)
    return lines


def stats_from_stream(lines: list[dict[str, Any]]) -> AgentRunStats:
    """Summarise a stream-json run: result metadata + behaviour signals."""
    messages = ClaudeCodeProvider().messages_from_raw(lines)
    result = next((line for line in reversed(lines) if line.get("type") == "result"), {})
    usage = result.get("usage") or {}
    is_error = bool(result.get("is_error")) or not result
    return AgentRunStats(
        session_id=str(result.get("session_id", "")),
        num_turns=int(result.get("num_turns", 0) or 0),
        duration_seconds=float(result.get("duration_ms", 0) or 0) / 1000,
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        cost_usd_estimate=float(result.get("total_cost_usd", 0) or 0),
        is_error=is_error,
        error="" if result else "no result record in agent output",
        signals=compute_signals(messages),
    )


# ---------------------------------------------------------------------------
# Workspace + checker
# ---------------------------------------------------------------------------

def prepare_workspace(suite: BenchSuite, arm: BenchArm, parent: Path) -> Path:
    """Copy the suite project into a fresh directory and install the arm's file."""
    workspace = Path(tempfile.mkdtemp(prefix=BENCH_DIR_MARKER, dir=parent)) / "repo"
    shutil.copytree(suite.project_dir, workspace, ignore=_IGNORED_COPY)
    if arm.skill_text.strip():
        (workspace / INSTRUCTION_FILE).write_text(arm.skill_text)
    return workspace


def run_check(suite: BenchSuite, task: BenchTask, workspace: Path) -> tuple[bool, dict[str, bool], str]:
    """Run the suite checker; return (passed, criteria, human-readable output)."""
    command = suite.check.format(
        python=shlex.quote(sys.executable),
        suite_dir=shlex.quote(suite.suite_dir),
        workspace=shlex.quote(str(workspace)),
        task_id=shlex.quote(task.id),
    )
    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_CHECK_TIMEOUT,
            env=agent_env(),
        )
    except subprocess.TimeoutExpired:
        return False, {}, "checker timed out"
    output_lines = proc.stdout.strip().splitlines()
    criteria: dict[str, bool] = {}
    if output_lines:
        try:
            criteria = json.loads(output_lines[-1]).get("criteria", {})
            output_lines = output_lines[:-1]
        except (json.JSONDecodeError, AttributeError):
            criteria = {}
    feedback = "\n".join(output_lines) or proc.stderr.strip()[-800:]
    return proc.returncode == 0, criteria, feedback


# ---------------------------------------------------------------------------
# One (task, arm, seed) run, with optional reviewer feedback rounds
# ---------------------------------------------------------------------------

def execute_run(
    suite: BenchSuite,
    task: BenchTask,
    arm: BenchArm,
    seed: int,
    config: RunConfig,
    scratch: Path,
) -> BenchRunResult:
    """Run the agent on one task, check it, and optionally replay feedback."""
    started = now_utc()
    workspace = prepare_workspace(suite, arm, scratch)
    persist = config.feedback_rounds > 0
    transcript: list[dict[str, Any]] = []
    attempts: list[AgentRunStats] = []
    try:
        stats, lines = run_agent(
            workspace, task.prompt, config.model, suite.timeout_seconds, persist=persist
        )
        attempts.append(stats)
        transcript.extend(lines)
        passed, criteria, feedback = run_check(suite, task, workspace)
        first_passed = passed

        for _ in range(config.feedback_rounds):
            if passed or not stats.session_id:
                break
            message = FEEDBACK_PREFIX + feedback
            transcript.append(_user_line(message, stats.session_id))
            stats, lines = run_agent(
                workspace, message, config.model, suite.timeout_seconds,
                resume=stats.session_id, persist=True,
            )
            attempts.append(stats)
            transcript.extend(lines)
            passed, criteria, feedback = run_check(suite, task, workspace)
    finally:
        if persist:
            _remove_persisted_sessions(workspace)
        if not config.keep_workspaces:
            shutil.rmtree(workspace.parent, ignore_errors=True)

    transcript_path = _write_transcript(config.out_dir, task.id, arm.name, seed, task.prompt, transcript)
    return BenchRunResult(
        task_id=task.id,
        split=task.split,
        arm=arm.name,
        seed=seed,
        passed=passed,
        first_attempt_passed=first_passed,
        criteria=criteria,
        check_output=feedback,
        attempts=attempts,
        transcript_path=str(transcript_path),
        model=config.model,
        started_at=started,
        finished_at=now_utc(),
    )


def _remove_persisted_sessions(workspace: Path) -> None:
    """Delete the ~/.claude/projects folder Claude Code created for a bench workspace.

    Feedback rounds need session persistence (for --resume); the transcript is
    already captured in the run directory, so the copy under ~/.claude/projects
    is removed.  Only folders whose name carries the bench prefix are touched.
    """
    folder_name = re.sub(r"[^A-Za-z0-9]", "-", str(workspace.resolve()))
    if BENCH_DIR_MARKER not in folder_name:
        return
    target = Path(CLAUDE_PROJECTS_ROOT).expanduser() / folder_name
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


def _user_line(text: str, session_id: str) -> dict[str, Any]:
    return {"type": "user", "sessionId": session_id, "message": {"role": "user", "content": text}}


def _write_transcript(
    out_dir: Path, task_id: str, arm: str, seed: int, prompt: str, lines: list[dict[str, Any]]
) -> Path:
    opening = _user_line(prompt, "")
    return store.write_bench_transcript(out_dir, f"{task_id}__{arm}__{seed}", [opening, *lines])


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


# ---------------------------------------------------------------------------
# Matrix execution (parallel, resumable)
# ---------------------------------------------------------------------------

def load_results(out_dir: Path) -> list[BenchRunResult]:
    """Load all results recorded so far in out_dir."""
    return store.load_bench_results(out_dir)


def run_matrix(
    suite: BenchSuite,
    tasks: list[BenchTask],
    arms: list[BenchArm],
    seeds: int,
    config: RunConfig,
    workers: int,
    on_result: Callable[[BenchRunResult], None] | None = None,
    on_error: Callable[[str, BaseException], None] | None = None,
) -> list[BenchRunResult]:
    """Run every (task, arm, seed) not already in results.jsonl, in parallel.

    Infrastructure failures (not agent failures) are reported via on_error and
    left out of results.jsonl, so re-running the same command retries them.
    """
    config.out_dir.mkdir(parents=True, exist_ok=True)
    done = {result.key for result in load_results(config.out_dir)}
    planned = [
        (task, arm, seed)
        for seed in range(seeds)
        for task in tasks
        for arm in arms
        if f"{task.id}|{arm.name}|{seed}" not in done
    ]
    lock = threading.Lock()
    new_results: list[BenchRunResult] = []

    scratch = Path(tempfile.mkdtemp(prefix="skillrunes-bench-"))
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = {
            pool.submit(execute_run, suite, task, arm, seed, config, scratch): (task, arm, seed)
            for task, arm, seed in planned
        }
        for future in as_completed(futures):
            task, arm, seed = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - surfaced to caller
                if on_error is not None:
                    on_error(f"{task.id}|{arm.name}|{seed}", exc)
                continue
            with lock:
                store.append_bench_result(config.out_dir, result)
                new_results.append(result)
            if on_result is not None:
                on_result(result)
    except KeyboardInterrupt:
        # Don't start (and bill) queued runs; in-flight runs finish or time out.
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        if not config.keep_workspaces:
            shutil.rmtree(scratch, ignore_errors=True)
    return new_results
