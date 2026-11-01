import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import skillrunes.bench.runner as runner
from skillrunes.bench.report import build_report, gate_decision
from skillrunes.bench.suite import load_suite, parse_arm, select_tasks
from skillrunes.models import AgentRunStats, BenchArm, BenchRunResult, SessionSignals

SUITE = Path(__file__).resolve().parents[1] / "bench" / "invoicing" / "suite.toml"
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# suite + arms
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_shipped_suite_loads_with_train_test_split() -> None:
    suite = load_suite(SUITE)

    assert suite.name == "invoicing"
    assert len(select_tasks(suite, "train")) == 5
    assert len(select_tasks(suite, "test")) == 8
    assert [t.id for t in select_tasks(suite, "test", ["csv"])] == ["csv"]


@pytest.mark.unit
def test_select_tasks_rejects_unknown_ids_and_splits() -> None:
    suite = load_suite(SUITE)
    with pytest.raises(ValueError, match="Unknown task"):
        select_tasks(suite, "test", ["tax"])  # tax is a train task
    with pytest.raises(ValueError, match="split"):
        select_tasks(suite, "validation")


@pytest.mark.unit
def test_suite_validation_errors(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    path = tmp_path / "suite.toml"
    path.write_text('[suite]\nname="x"\ncheck="true"\n[[tasks]]\nid="a"\nprompt="p"\n[[tasks]]\nid="a"\nprompt="q"\n')
    with pytest.raises(ValueError, match="Duplicate"):
        load_suite(path)
    path.write_text('[suite]\nname="x"\n[[tasks]]\nid="a"\nprompt="p"\n')
    with pytest.raises(ValueError, match="check"):
        load_suite(path)
    path.write_text("not = [valid")
    with pytest.raises(ValueError, match="Could not read"):
        load_suite(path)


@pytest.mark.unit
def test_parse_arm(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("- always update the changelog")

    assert parse_arm("none") == BenchArm(name="none")
    arm = parse_arm(f"learned={skill}")
    assert arm.name == "learned" and "changelog" in arm.skill_text
    with pytest.raises(ValueError, match="needs a file"):
        parse_arm("learned")
    with pytest.raises(ValueError, match="not found"):
        parse_arm("x=/missing.md")


# ---------------------------------------------------------------------------
# runner: command isolation, env, stream parsing
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_agent_command_is_isolated() -> None:
    cmd = runner.agent_command("do it", "sonnet")

    assert cmd[:3] == ["claude", "-p", "do it"]
    assert cmd[cmd.index("--setting-sources") + 1] == "project"
    assert "--strict-mcp-config" in cmd
    assert "--no-session-persistence" in cmd
    assert "--dangerously-skip-permissions" not in cmd
    resumed = runner.agent_command("fix", "sonnet", resume="abc", persist=True)
    assert resumed[resumed.index("--resume") + 1] == "abc"
    assert "--no-session-persistence" not in resumed


@pytest.mark.unit
def test_agent_env_strips_api_key_and_prefers_harness_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    env = runner.agent_env()

    assert "ANTHROPIC_API_KEY" not in env
    assert env["PATH"].startswith(str(Path(sys.executable).parent))


def _stream(errors: int = 1, is_error: bool = False) -> str:
    lines: list[dict[str, Any]] = [{"type": "system", "subtype": "init"}]
    for i in range(errors):
        lines.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "pytest"}}]}})
        lines.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True, "content": "boom"}]}})
    lines.append({
        "type": "result", "session_id": "sess-1", "num_turns": 3, "duration_ms": 1500,
        "total_cost_usd": 0.01, "is_error": is_error,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
                  "cache_creation_input_tokens": 20},
    })
    return "noise\n" + "\n".join(json.dumps(line) for line in lines) + "\n{broken"


@pytest.mark.unit
def test_stats_from_stream() -> None:
    stats = runner.stats_from_stream(runner.parse_stream(_stream(errors=2)))

    assert stats.session_id == "sess-1"
    assert stats.num_turns == 3
    assert stats.total_tokens == 135
    assert stats.duration_seconds == 1.5
    assert stats.signals.tool_errors == 2
    assert stats.signals.retry_loops == 1
    assert not stats.is_error


@pytest.mark.unit
def test_missing_result_is_agent_error() -> None:
    stats = runner.stats_from_stream([])

    assert stats.is_error
    assert "no result" in stats.error


# ---------------------------------------------------------------------------
# runner: full execute_run with the real checker and a fake agent
# ---------------------------------------------------------------------------

_REAL_RUN = subprocess.run


def _fake_agent(edits: dict[str, str], calls: list[list[str]]):
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] != "claude":
            return _REAL_RUN(cmd, **kwargs)  # the real checker
        calls.append(cmd)
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        workspace = Path(kwargs["cwd"])
        round_edits = edits if len(calls) == 1 else {"CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n- Added refund.\n"}
        for rel, text in round_edits.items():
            target = workspace / rel
            target.write_text(target.read_text() + text if rel.endswith(".py") else text)
        return subprocess.CompletedProcess(cmd, 0, _stream(errors=0), "")

    return fake_run


REFUND_IMPL = '''

from datetime import date as _date

from invoicing.errors import PaymentError
from invoicing.models import Payment


def refund(invoice: Invoice, amount: Decimal, on: _date) -> Invoice:
    """Record a refund as a negative payment."""
    if amount <= 0 or amount > invoice.paid():
        raise PaymentError("invalid refund")
    return replace(invoice, payments=(*invoice.payments, Payment(-to_money(amount), on)))
'''


@pytest.mark.integration
def test_execute_run_with_feedback_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(runner.subprocess, "run", _fake_agent({"invoicing/calc.py": REFUND_IMPL}, calls))
    suite = load_suite(SUITE)
    task = next(t for t in suite.tasks if t.id == "refund")
    config = runner.RunConfig(model="sonnet", out_dir=tmp_path / "out", feedback_rounds=1)

    result = runner.execute_run(suite, task, BenchArm(name="none"), 0, config, tmp_path)

    assert not result.first_attempt_passed  # changelog missing on first try
    assert result.passed  # fixed after reviewer feedback
    assert len(calls) == 2 and "--resume" in calls[1]
    assert result.criteria["functional"] and result.criteria["changelog"]
    transcript = Path(result.transcript_path).read_text().splitlines()
    assert json.loads(transcript[0])["message"]["content"] == task.prompt
    assert any("reviewer looked at your change" in line for line in transcript)


@pytest.mark.integration
def test_skill_arm_is_installed_as_claude_md(tmp_path: Path) -> None:
    suite = load_suite(SUITE)

    workspace = runner.prepare_workspace(suite, BenchArm(name="s", skill_text="RULES"), tmp_path)

    assert (workspace / "CLAUDE.md").read_text() == "RULES"
    assert not (workspace / ".pytest_cache").exists()
    assert not (runner.prepare_workspace(suite, BenchArm(name="none"), tmp_path) / "CLAUDE.md").exists()


@pytest.mark.integration
def test_run_matrix_is_resumable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[str] = []

    def fake_execute(suite, task, arm, seed, config, scratch):  # noqa: ANN001
        executed.append(f"{task.id}|{arm.name}|{seed}")
        if task.id == "csv":
            raise RuntimeError("infra")
        return _result(task.id, arm.name, seed, passed=True)

    monkeypatch.setattr(runner, "execute_run", fake_execute)
    suite = load_suite(SUITE)
    tasks = select_tasks(suite, "test", ["refund", "csv"])
    config = runner.RunConfig(model="sonnet", out_dir=tmp_path)
    errors: list[str] = []

    runner.run_matrix(suite, tasks, [BenchArm(name="none")], 2, config, 2, on_error=lambda k, e: errors.append(k))
    assert sorted(errors) == ["csv|none|0", "csv|none|1"]
    assert len(runner.load_results(tmp_path)) == 2

    executed.clear()
    runner.run_matrix(suite, tasks, [BenchArm(name="none")], 2, config, 2)
    assert sorted(executed) == ["csv|none|0", "csv|none|1"]  # only the missing runs retried


# ---------------------------------------------------------------------------
# report + gate
# ---------------------------------------------------------------------------

def _result(task: str, arm: str, seed: int, passed: bool, tokens: int = 1000) -> BenchRunResult:
    attempt = AgentRunStats(num_turns=5, input_tokens=tokens, signals=SessionSignals(tool_errors=1))
    return BenchRunResult(
        task_id=task, split="test", arm=arm, seed=seed, passed=passed, first_attempt_passed=passed,
        criteria={"changelog": passed, "functional": True}, attempts=[attempt],
        started_at=T0, finished_at=T0,
    )


def _matrix(candidate_pass: bool) -> list[BenchRunResult]:
    tasks = [f"t{i}" for i in range(8)]
    return [
        r
        for task in tasks
        for seed in range(3)
        for r in (
            _result(task, "none", seed, passed=False, tokens=2000),
            _result(task, "learned", seed, passed=candidate_pass, tokens=1500),
        )
    ]


@pytest.mark.unit
def test_report_and_gate_promote_clear_win() -> None:
    report = build_report(_matrix(candidate_pass=True), "invoicing", "test", baseline="none")

    learned = next(a for a in report.arms if a.arm == "learned")
    assert learned.pass_rate == 1.0
    assert learned.criteria_rates == {"changelog": 1.0, "functional": 1.0}
    (comparison,) = report.comparisons
    assert comparison.pairs == 24
    assert comparison.pass_rate_delta == 1.0
    assert comparison.tokens_delta == -500
    promote, reason = gate_decision(report, "learned")
    assert promote and "+100.0%" in reason


@pytest.mark.unit
def test_gate_rejects_regression_and_missing_arm() -> None:
    results = _matrix(candidate_pass=False)
    results = [r.model_copy(update={"passed": True}) if r.arm == "none" else r for r in results]
    report = build_report(results, "invoicing", "test", baseline="none")

    assert gate_decision(report, "learned")[0] is False
    assert gate_decision(report, "ghost") == (False, "No paired comparison for arm 'ghost'.")


@pytest.mark.unit
def test_gate_rejects_wide_interval() -> None:
    results = [
        _result("a", "none", 0, passed=False),
        _result("a", "learned", 0, passed=True),
        _result("b", "none", 0, passed=True),
        _result("b", "learned", 0, passed=False),
        _result("c", "none", 0, passed=False),
        _result("c", "learned", 0, passed=True),
    ]
    report = build_report(results, "s", "test", baseline="none")

    promote, reason = gate_decision(report, "learned")
    assert not promote and "non-inferiority" in reason
