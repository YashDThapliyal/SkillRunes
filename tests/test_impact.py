import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

import skillrunes.store as store
from skillrunes.cli import app
from skillrunes.impact import backfill_summary, build_impact_report
from skillrunes.ingest.claude_code import ClaudeCodeProvider
from skillrunes.models import SessionSignals, SessionSummary, SkillVersion
from skillrunes.stats import bootstrap_mean_diff, bootstrap_paired_diff, mean

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _summary(sid: str, day: int, errors: int, calls: int = 10, retries: int = 0) -> SessionSummary:
    return SessionSummary(
        session_id=sid,
        goal="g",
        analyzed_at=T0 + timedelta(days=30),
        started_at=T0 + timedelta(days=day),
        token_count=1000,
        signals=SessionSignals(tool_calls=calls, tool_errors=errors, retry_loops=retries, user_turns=4),
    )


def _version(n: int, day: int) -> SkillVersion:
    return SkillVersion(version=n, timestamp=T0 + timedelta(days=day), content=f"v{n}")


@pytest.mark.unit
def test_bootstrap_detects_clear_difference() -> None:
    interval = bootstrap_mean_diff([5.0, 6.0, 5.5, 6.5, 5.0], [1.0, 1.5, 0.5, 1.0, 1.2])

    assert interval.high < 0
    assert interval.excludes_zero()
    assert interval.estimate == pytest.approx(mean([1.0, 1.5, 0.5, 1.0, 1.2]) - 5.6)


@pytest.mark.unit
def test_bootstrap_is_reproducible_and_overlapping_is_inconclusive() -> None:
    a, b = [1.0, 3.0, 2.0, 5.0], [2.0, 1.0, 4.0, 3.0]

    first = bootstrap_mean_diff(a, b)
    assert first == bootstrap_mean_diff(a, b)
    assert not first.excludes_zero()


@pytest.mark.unit
def test_paired_bootstrap() -> None:
    interval = bootstrap_paired_diff([(0.0, 1.0)] * 6 + [(1.0, 1.0)] * 2)

    assert interval.estimate == pytest.approx(0.75)
    assert interval.low > 0


@pytest.mark.unit
def test_bootstrap_rejects_empty() -> None:
    with pytest.raises(ValueError):
        bootstrap_mean_diff([], [1.0])
    with pytest.raises(ValueError):
        bootstrap_paired_diff([])


@pytest.mark.unit
def test_sessions_are_bucketed_by_active_version() -> None:
    summaries = [_summary("a", 1, 5), _summary("b", 2, 5), _summary("c", 6, 1), _summary("d", 12, 0)]
    history = [_version(1, 5), _version(2, 10)]

    report = build_impact_report(summaries, history, min_sessions=1)

    assert [(c.version, c.session_ids) for c in report.cohorts] == [
        (0, ["a", "b"]),
        (1, ["c"]),
        (2, ["d"]),
    ]
    assert report.cohorts[0].metrics["tool_error_rate"] == 0.5
    assert report.cohorts[1].active_until == T0 + timedelta(days=10)


@pytest.mark.unit
def test_improvement_verdict_with_enough_sessions() -> None:
    before = [_summary(f"b{i}", 1, 6 + i % 2, retries=2) for i in range(5)]
    after = [_summary(f"a{i}", 8, i % 2, retries=0) for i in range(5)]

    report = build_impact_report(before + after, [_version(1, 5)], min_sessions=3)

    verdicts = {d.metric: d.verdict for d in report.deltas}
    assert verdicts["tool_error_rate"] == "improved"
    assert verdicts["retries_per_session"] == "improved"
    assert verdicts["tokens_per_session"] == "inconclusive"


@pytest.mark.unit
def test_small_cohorts_report_insufficient_data() -> None:
    report = build_impact_report(
        [_summary("a", 1, 5), _summary("b", 8, 0)], [_version(1, 5)], min_sessions=3
    )

    assert all(d.verdict == "insufficient_data" and d.ci_low is None for d in report.deltas)


@pytest.mark.unit
def test_unmeasured_sessions_are_counted() -> None:
    legacy = SessionSummary(session_id="old", goal="g", analyzed_at=T0)

    report = build_impact_report([legacy, _summary("a", 1, 1)], [])

    assert report.unmeasured_sessions == 1
    assert [c.version for c in report.cohorts] == [0]


@pytest.mark.unit
def test_naive_timestamps_do_not_crash() -> None:
    summary = _summary("a", 1, 1).model_copy(update={"started_at": datetime(2026, 10, 9)})

    report = build_impact_report([summary], [_version(1, 5)])

    assert report.cohorts[0].version == 1


@pytest.mark.unit
def test_backfill_rebuilds_signals_from_raw_lines() -> None:
    legacy = SessionSummary(session_id="old", goal="g", analyzed_at=T0)
    raw = [
        {"type": "user", "timestamp": "2026-10-02T00:00:00Z", "message": {"role": "user", "content": "go"}},
        {
            "type": "assistant",
            "timestamp": "2026-10-02T00:00:05Z",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]},
        },
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "is_error": True}]},
        },
    ]

    refreshed = backfill_summary(legacy, raw, ClaudeCodeProvider().messages_from_raw)

    assert refreshed is not None
    assert refreshed.signals is not None and refreshed.signals.tool_errors == 1
    assert refreshed.started_at == datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert legacy.signals is None  # original untouched
    assert backfill_summary(refreshed, raw, ClaudeCodeProvider().messages_from_raw) is None
    assert backfill_summary(legacy, None, ClaudeCodeProvider().messages_from_raw) is None


@pytest.mark.integration
def test_impact_cli_backfills_and_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store.init_store()
    store.save_session_summary(SessionSummary(session_id="s1", goal="g", analyzed_at=T0))
    store.archive_raw_session(
        "s1",
        [{"type": "user", "timestamp": "2026-10-02T00:00:00Z", "message": {"role": "user", "content": "hi"}}],
    )

    result = CliRunner().invoke(app, ["impact"])

    assert result.exit_code == 0, result.output
    assert "baseline" in result.output
    saved = json.loads((tmp_path / ".skillrunes" / "impact.json").read_text())
    assert saved["cohorts"][0]["session_ids"] == ["s1"]
    assert store.load_all_summaries()[0].signals is not None


@pytest.mark.integration
def test_impact_cli_with_no_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["impact"])

    assert result.exit_code == 0
    assert "No measurable sessions" in result.output
