from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import skillrunes.store as store
from skillrunes.dashboard.server import app
from skillrunes.models import SessionSignals, SessionSummary

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store.init_store()
    for i in range(4):
        store.save_session_summary(
            SessionSummary(
                session_id=f"s{i}",
                goal="ship feature",
                what_worked=["tests pass"],
                what_failed=["lint failed"] if i % 2 else [],
                analyzed_at=T0 + timedelta(days=i),
                started_at=T0 + timedelta(days=i),
                token_count=1000 * (i + 1),
                message_count=10,
                signals=SessionSignals(tool_calls=10, tool_errors=4 - i, user_turns=3),
            )
        )


@pytest.mark.integration
def test_index_renders_impact_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, monkeypatch)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "Skill impact" in response.text
    assert "impactBar" in response.text


@pytest.mark.integration
def test_index_renders_empty_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "Skill impact" not in response.text


@pytest.mark.integration
def test_api_impact_returns_cohorts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, monkeypatch)

    payload = TestClient(app).get("/api/impact").json()

    assert payload["cohorts"][0]["version"] == 0
    assert len(payload["cohorts"][0]["session_ids"]) == 4


@pytest.mark.integration
def test_api_metrics_defaults_on_fresh_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert TestClient(app).get("/api/metrics").json()["sessions_analyzed"] == 0
    assert TestClient(app).get("/api/skills").json() == []
