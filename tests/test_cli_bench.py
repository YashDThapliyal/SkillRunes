import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import skillrunes.cli_bench as cli_bench
import skillrunes.ingest.claude_code as claude_code
from skillrunes.billing import BillingError
from skillrunes.cli import app
from skillrunes.models import AgentRunStats, BenchRunResult

SUITE = str(Path(__file__).resolve().parents[1] / "bench" / "invoicing" / "suite.toml")
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
runner = CliRunner()


def _result(task: str, arm: str, seed: int, passed: bool) -> BenchRunResult:
    return BenchRunResult(
        task_id=task, split="test", arm=arm, seed=seed, passed=passed, first_attempt_passed=passed,
        criteria={"functional": True, "changelog": passed},
        attempts=[AgentRunStats(num_turns=4, input_tokens=100)], started_at=T0, finished_at=T0,
    )


@pytest.fixture
def subscribed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_bench, "require_subscription_auth", lambda: None)


@pytest.mark.integration
def test_bench_run_refuses_without_subscription(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def deny() -> None:
        raise BillingError("not logged in")

    monkeypatch.setattr(cli_bench, "require_subscription_auth", deny)

    result = runner.invoke(app, ["bench", "run", "--suite", SUITE, "--out", str(tmp_path)])

    assert result.exit_code == 1
    assert "Billing guard" in result.output


@pytest.mark.integration
def test_bench_run_plans_and_reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subscribed: None) -> None:
    captured: dict[str, Any] = {}

    def fake_matrix(suite, tasks, arms, seeds, config, workers, on_result=None, on_error=None):  # noqa: ANN001, ANN202
        captured.update(tasks=[t.id for t in tasks], arms=[a.name for a in arms], seeds=seeds, config=config)
        result = _result(tasks[0].id, "none", 0, passed=False)
        on_result(result)
        on_error("x|none|0", RuntimeError("boom"))
        from skillrunes import store

        store.append_bench_result(config.out_dir, result)
        return [result]

    monkeypatch.setattr(cli_bench, "run_matrix", fake_matrix)
    skill = tmp_path / "skill.md"
    skill.write_text("- update CHANGELOG.md")

    result = runner.invoke(app, [
        "bench", "run", "--suite", SUITE, "--task", "refund", "--arm", "none",
        "--arm", f"learned={skill}", "--seeds", "2", "--out", str(tmp_path / "runs"),
    ])

    assert result.exit_code == 0, result.output
    assert captured["tasks"] == ["refund"] and captured["arms"] == ["none", "learned"]
    assert captured["config"].feedback_rounds == 0  # test split default
    assert "4 of 4 runs pending" in result.output
    assert "fail refund" in result.output and "infra error" in result.output
    assert "Pass rate" in result.output


@pytest.mark.integration
def test_bench_run_train_defaults_to_one_feedback_round(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subscribed: None
) -> None:
    rounds: list[int] = []
    monkeypatch.setattr(
        cli_bench, "run_matrix",
        lambda suite, tasks, arms, seeds, config, workers, **k: rounds.append(config.feedback_rounds) or [],
    )

    result = runner.invoke(app, ["bench", "run", "--suite", SUITE, "--split", "train", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert rounds == [1]
    assert "No results for this split" in result.output


@pytest.mark.integration
@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--arm", "none", "--arm", "none"], "unique"),
        (["--arm", "learned"], "needs a file"),
        (["--task", "nope"], "Unknown task"),
    ],
)
def test_bench_run_validation(tmp_path: Path, args: list[str], message: str) -> None:
    result = runner.invoke(app, ["bench", "run", "--suite", SUITE, "--out", str(tmp_path), *args])

    assert result.exit_code == 1
    assert message in result.output


@pytest.mark.integration
def test_bench_report_gate(tmp_path: Path) -> None:
    from skillrunes import store

    for task in ("a", "b", "c", "d", "e", "f"):
        for seed in range(3):
            store.append_bench_result(tmp_path, _result(task, "none", seed, passed=False))
            store.append_bench_result(tmp_path, _result(task, "learned", seed, passed=True))

    promote = runner.invoke(app, ["bench", "report", "--out", str(tmp_path), "--gate", "learned"])
    reject = runner.invoke(app, ["bench", "report", "--out", str(tmp_path), "--gate", "none", "--baseline", "learned"])
    as_json = runner.invoke(app, ["bench", "report", "--out", str(tmp_path), "--json"])

    assert promote.exit_code == 0 and "PROMOTE" in promote.output
    assert reject.exit_code == 2 and "REJECT" in reject.output
    assert json.loads(as_json.output)["arms"][0]["arm"] == "learned"
    assert (tmp_path / "report-test.json").exists()


@pytest.mark.integration
def test_bench_report_without_results(tmp_path: Path) -> None:
    result = runner.invoke(app, ["bench", "report", "--out", str(tmp_path)])

    assert result.exit_code == 1
    assert "No results" in result.output


@pytest.mark.integration
def test_bench_learn_and_oneshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subscribed: None) -> None:
    monkeypatch.setattr(cli_bench, "learn_skill", lambda results, **k: ("# learned\n", ["s"]))
    monkeypatch.setattr(cli_bench, "generate_oneshot", lambda suite, model: "# oneshot\n")

    learned = runner.invoke(app, ["bench", "learn", "--out", str(tmp_path), "--synthesis", "delta"])
    oneshot = runner.invoke(app, ["bench", "oneshot", "--suite", SUITE, "--out", str(tmp_path)])
    bad = runner.invoke(app, ["bench", "learn", "--out", str(tmp_path), "--synthesis", "magic"])

    assert learned.exit_code == 0, learned.output
    assert (tmp_path / "skillrunes-delta-skill.md").read_text() == "# learned\n"
    assert oneshot.exit_code == 0, oneshot.output
    assert (tmp_path / "oneshot-claude.md").read_text() == "# oneshot\n"
    assert bad.exit_code == 1


@pytest.mark.integration
def test_bench_learn_and_oneshot_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subscribed: None) -> None:
    def no_train(results, **k):  # noqa: ANN001, ANN202
        raise ValueError("No train-split transcripts found")

    def no_file(suite, model):  # noqa: ANN001, ANN202
        raise RuntimeError("Agent did not write CLAUDE.md")

    monkeypatch.setattr(cli_bench, "learn_skill", no_train)
    monkeypatch.setattr(cli_bench, "generate_oneshot", no_file)

    assert "No train-split" in runner.invoke(app, ["bench", "learn", "--out", str(tmp_path)]).output
    assert runner.invoke(app, ["bench", "oneshot", "--suite", SUITE]).exit_code == 1
    assert runner.invoke(app, ["bench", "oneshot", "--suite", str(tmp_path / "missing.toml")]).exit_code == 1


# ---------------------------------------------------------------------------
# Claude Code provider discovery
# ---------------------------------------------------------------------------

def _jsonl(path: Path, cwd: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "user", "cwd": cwd, "message": {"role": "user", "content": "hi"}}) + "\n")


@pytest.mark.integration
def test_claude_code_provider_finds_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    cwd = "/work/demo"
    project = root / "-work-demo"
    _jsonl(project / "a.jsonl", cwd)
    _jsonl(project / "other.jsonl", "/work/demo-other")
    _jsonl(project / "sess" / "subagents" / "agent-1.jsonl", cwd)
    _jsonl(root / "-elsewhere" / "z.jsonl", cwd)
    monkeypatch.setattr(claude_code, "CLAUDE_PROJECTS_ROOT", str(root))

    found = claude_code.ClaudeCodeProvider().find_session_files(Path(cwd))

    assert sorted(p.name for p in found) == ["a.jsonl", "agent-1.jsonl"]


@pytest.mark.integration
def test_claude_code_provider_reads_sessions_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    cwd = "/work/demo"
    project = root / "-work-demo"
    _jsonl(project / "full.jsonl", cwd)
    _jsonl(project / "s2" / "subagents" / "agent-2.jsonl", cwd)
    project.mkdir(parents=True, exist_ok=True)
    (project / "sessions-index.json").write_text(json.dumps({"entries": [
        {"fullPath": str(project / "full.jsonl"), "projectPath": cwd},
        {"fullPath": "/missing.jsonl", "sessionId": "s2", "projectPath": cwd},
        {"fullPath": str(project / "full.jsonl"), "projectPath": "/other"},
        "garbage",
    ]}))
    monkeypatch.setattr(claude_code, "CLAUDE_PROJECTS_ROOT", str(root))

    found = claude_code.ClaudeCodeProvider().find_session_files(Path(cwd))

    assert sorted(p.name for p in found) == ["agent-2.jsonl", "full.jsonl"]


@pytest.mark.integration
def test_claude_code_provider_handles_missing_root_and_bad_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_code, "CLAUDE_PROJECTS_ROOT", str(tmp_path / "nope"))
    provider = claude_code.ClaudeCodeProvider()
    assert provider.find_session_files(Path("/x")) == []

    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"type": "user", "message": {"role": "user", "content": "ok"}}\n{oops\n\n')
    messages, raw = provider.parse_session_file(bad)
    assert len(messages) == 1 and len(raw) == 1
    assert provider.parse_session_file(tmp_path / "missing.jsonl") == ([], [])
    assert provider.expected_location(Path("/a/b")) == "~/.claude/projects/-a-b"
