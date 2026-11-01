import os
from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

import skillrunes.store as store
from skillrunes.cli import app
from skillrunes.compress import compress_skill_content, estimate_tokens
from skillrunes.exporters import AGENTS_BEGIN, build_agents_update, build_claude_skill
from skillrunes.models import ProjectMetrics, SessionSummary, SkillVersion, TimePoint
from skillrunes.redact import redact_text
from skillrunes.traces import build_trace_exports
from skillrunes.utils import now_utc


def test_basic_redaction_masks_common_secrets() -> None:
    text = (
        "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456 "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456 "
        "https://user:pass@example.com/repo.git"
    )

    redacted, counts = redact_text(text, "basic")

    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "Bearer abcdef" not in redacted
    assert "user:pass" not in redacted
    assert counts["openai_key"] == 1
    assert counts["bearer_token"] == 1
    assert counts["credential_url"] == 1


def test_strict_redaction_masks_email_and_external_paths() -> None:
    redacted, counts = redact_text(
        "email me at test@example.com and read /tmp/private/file.txt",
        "strict",
        Path("/Users/yash/Documents/AgentLens"),
    )

    assert "test@example.com" not in redacted
    assert "/tmp/private/file.txt" not in redacted
    assert counts["email"] == 1
    assert counts["external_path"] == 1


def test_strict_redaction_handles_credential_urls_and_queries() -> None:
    text = (
        "https://user:pass@example.com/path?token=abc "
        "https://api_key@example.com/path?x=1 "
        "postgres://user:pass@localhost:5432/db?sslmode=require"
    )

    redacted, counts = redact_text(text, "strict")
    redacted_again, counts_again = redact_text(redacted, "strict")

    assert "user:pass" not in redacted
    assert "token=abc" not in redacted
    assert "sslmode=require" not in redacted
    assert "https://api_key@example.com/path?x=1" not in redacted
    assert counts["credential_url"] == 2
    assert counts["url_query"] == 3
    assert redacted_again == redacted
    assert counts_again == {}


def test_compression_respects_budget_and_preserves_reference() -> None:
    content = "\n".join(
        ["# Skill"] + [f"- Always remember important failure pattern {i}" for i in range(40)]
    )

    compressed, reference, report = compress_skill_content(
        content,
        budget_tokens=80,
        mode="balanced",
    )

    assert estimate_tokens(compressed) <= 80
    assert reference.startswith("# SkillRunes Reference Overflow")
    assert report.moved_count > 0


def test_export_builders_generate_claude_skill_and_preserve_agents_content() -> None:
    skill = SkillVersion(
        version=2,
        timestamp=now_utc(),
        content="## Patterns\n- Run tests before finishing.",
    )

    files, metadata = build_claude_skill(skill, "Detailed history")
    skill_path = Path(".claude/skills/skillrunes-project-memory/SKILL.md")

    assert skill_path in files
    assert "name: skillrunes-project-memory" in files[skill_path]
    assert any(item.target == "claude" for item in metadata)

    existing = "# Project Rules\n\nKeep this."
    updated = build_agents_update(existing, skill)

    assert "Keep this." in updated
    assert AGENTS_BEGIN in updated
    assert "Run tests before finishing." in updated


def test_eval_latest_cli_writes_artifact() -> None:
    runner = CliRunner()
    with TemporaryDirectory() as tmpdir:
        cwd = Path(tmpdir)
        old_cwd = Path.cwd()
        try:
            os.chdir(cwd)
            store.init_store()
            store.save_skill_version("## Patterns\n- Old rule.", "old", token_estimate=10)
            store.save_skill_version("## Patterns\n- Old rule.\n- New reusable pattern.", "new", token_estimate=20)
            result = runner.invoke(app, ["eval", "--latest"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0
        assert list((cwd / ".skillrunes" / "evals").glob("*.json"))


def test_export_and_prune_dry_run_do_not_write_targets() -> None:
    runner = CliRunner()
    with TemporaryDirectory() as tmpdir:
        cwd = Path(tmpdir)
        old_cwd = Path.cwd()
        try:
            os.chdir(cwd)
            store.init_store()
            store.save_skill_version(
                "\n".join(["# Skill"] + [f"- Always run check {i}." for i in range(20)]),
                "seed",
                token_estimate=100,
            )
            export_result = runner.invoke(
                app,
                ["export", "--target", "all", "--budget-tokens", "80", "--dry-run"],
                catch_exceptions=False,
            )
            prune_result = runner.invoke(
                app,
                ["prune", "--budget-tokens", "80", "--dry-run"],
                catch_exceptions=False,
            )
        finally:
            os.chdir(old_cwd)

        assert export_result.exit_code == 0
        assert prune_result.exit_code == 0
        assert not (cwd / ".claude").exists()
        assert not (cwd / "AGENTS.md").exists()


def test_trace_exports_use_summary_metadata() -> None:
    summary = SessionSummary(
        session_id="abc123",
        goal="Fix tests",
        what_worked=["pytest passed"],
        patterns=["Run pytest"],
        token_count=100,
        duration_seconds=12.0,
        analyzed_at=now_utc(),
        message_count=4,
    )

    traces = build_trace_exports([summary], "claude_code")

    assert traces[0].session_id == "abc123"
    assert traces[0].spans[0]["attributes"]["skillrunes.provider"] == "claude_code"
    assert traces[0].spans[0]["attributes"]["gen_ai.usage.input_tokens"] == 100
    assert any(event["name"] == "skillrunes.skill_update" for event in traces[0].spans[0]["events"])


def test_save_and_load_metrics_round_trip() -> None:
    with TemporaryDirectory() as tmpdir:
        cwd = Path(tmpdir)
        old_cwd = Path.cwd()
        try:
            os.chdir(cwd)
            store.init_store()
            metrics = ProjectMetrics(
                sessions_analyzed=1,
                token_series=[TimePoint(date=now_utc(), value=42)],
            )
            store.save_metrics(metrics)
            loaded = store.load_metrics()
        finally:
            os.chdir(old_cwd)

        assert loaded is not None
        assert loaded.sessions_analyzed == 1
        assert loaded.token_series[0].value == 42


def test_trace_export_redacts_stored_summary_text() -> None:
    summary = SessionSummary(
        session_id="secret-session",
        goal="Fix OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
        what_failed=["Bearer abcdefghijklmnopqrstuvwxyz123456 leaked"],
        patterns=["Never paste sk-abcdefghijklmnopqrstuvwxyz123456 into traces"],
        token_count=10,
        duration_seconds=1.0,
        analyzed_at=now_utc(),
        message_count=1,
    )

    traces = build_trace_exports([summary], "claude_code", redaction_level="basic")
    trace_text = traces[0].model_dump_json()

    assert "sk-abcdefghijklmnopqrstuvwxyz" not in trace_text
    assert "Bearer abcdef" not in trace_text
    assert "[REDACTED" in trace_text


def test_doctor_privacy_basic_and_strict_levels() -> None:
    runner = CliRunner()
    with TemporaryDirectory() as tmpdir:
        cwd = Path(tmpdir)
        old_cwd = Path.cwd()
        try:
            os.chdir(cwd)
            store.init_store()
            Path("SKILL.md").write_text("Contact test@example.com from /tmp/private/file.txt", encoding="utf-8")
            basic = runner.invoke(app, ["doctor", "privacy", "--level", "basic"], catch_exceptions=False)
            strict = runner.invoke(app, ["doctor", "privacy", "--level", "strict"], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)

        assert basic.exit_code == 0
        assert strict.exit_code == 1
        assert "email" in strict.output
        assert "external_path" in strict.output
