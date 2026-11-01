import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import skillrunes.bench.learn as learn
import skillrunes.cli as cli
import skillrunes.store as store
import skillrunes.synthesize as synthesize
from skillrunes.curate import apply_deltas, render_rulebook, rulebook_for_prompt, similarity
from skillrunes.models import (
    AgentRunStats,
    BenchRunResult,
    RawMessage,
    RuleBook,
    RuleDelta,
    SessionSummary,
    SkillRule,
)

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _book(n: int = 4) -> RuleBook:
    rules = tuple(
        SkillRule(id=f"R{i}", section="Conventions", text=f"distinct rule number {i} about topic{i}")
        for i in range(1, n + 1)
    )
    return RuleBook(version=1, next_id=n + 1, rules=rules)


@pytest.mark.unit
def test_add_assigns_ids_and_provenance() -> None:
    book, report = apply_deltas(
        RuleBook(),
        [RuleDelta(op="add", section="Changelog", text="Add a bullet under Unreleased.", session_ids=("s1",))],
    )

    assert report.added == ["R1"]
    assert book.version == 1 and book.next_id == 2
    assert book.rules[0].source_sessions == ("s1",)
    assert book.rules[0].created_version == 1


@pytest.mark.unit
def test_inputs_are_not_mutated() -> None:
    original = _book()

    apply_deltas(original, [RuleDelta(op="remove", rule_id="R1")])

    assert len(original.rules) == 4 and original.version == 1


@pytest.mark.unit
def test_duplicate_add_becomes_helpful_vote() -> None:
    book = RuleBook(next_id=2, rules=(SkillRule(id="R1", section="S", text="Always update CHANGELOG.md under Unreleased"),))

    new, report = apply_deltas(
        book, [RuleDelta(op="add", text="always update the CHANGELOG.md under Unreleased", session_ids=("s9",))]
    )

    assert report.merged_duplicates == ["R1"] and not report.added
    assert new.rules[0].helpful == 1
    assert "s9" in new.rules[0].source_sessions


@pytest.mark.unit
def test_removal_cap_prevents_collapse() -> None:
    deltas = [RuleDelta(op="remove", rule_id=f"R{i}") for i in range(1, 5)]

    new, report = apply_deltas(_book(4), deltas, max_removal_fraction=0.25)

    assert report.removed == ["R1"]
    assert len(new.rules) == 3
    assert sum("removal cap" in r for r in report.rejected) == 3


@pytest.mark.unit
def test_harmful_votes_prune_rules() -> None:
    deltas = [RuleDelta(op="harmful", rule_id="R2"), RuleDelta(op="harmful", rule_id="R2")]

    new, report = apply_deltas(_book(), deltas)

    assert report.pruned == ["R2"]
    assert "R2" not in {r.id for r in new.rules}


@pytest.mark.unit
def test_update_and_rejections() -> None:
    deltas = [
        RuleDelta(op="update", rule_id="R1", text="sharper wording", session_ids=("s2",)),
        RuleDelta(op="update", rule_id="R2", text=" "),
        RuleDelta(op="helpful", rule_id="R99"),
        RuleDelta(op="explode", rule_id="R1"),
        RuleDelta(op="add", text=""),
    ]

    new, report = apply_deltas(_book(), deltas)

    assert report.updated == ["R1"]
    assert new.rules[0].text == "sharper wording" and new.rules[0].updated_version == 2
    assert len(report.rejected) == 4


@pytest.mark.unit
def test_render_groups_sections_and_keeps_ids_hidden() -> None:
    book, _ = apply_deltas(
        RuleBook(),
        [
            RuleDelta(op="add", section="Money", text="Use Decimal for money."),
            RuleDelta(op="add", section="Process", text="Update CHANGELOG.md."),
            RuleDelta(op="add", section="Money", text="Quantise to cents with to_money()."),
        ],
    )

    rendered = render_rulebook(book)

    assert rendered.index("## Money") < rendered.index("## Process")
    assert "- Use Decimal for money. <!-- R1 -->" in rendered
    assert rendered.count("## Money") == 1
    assert "[R2]" in rulebook_for_prompt(book)
    assert rulebook_for_prompt(RuleBook()) == "(no rules yet)"


@pytest.mark.unit
def test_similarity() -> None:
    assert similarity("Use Decimal for money", "use decimal for money") == 1.0
    assert similarity("Use Decimal", "Update the changelog") == 0.0
    assert similarity("", "x") == 0.0


# ---------------------------------------------------------------------------
# model-facing glue (client mocked)
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def extract_structured(self, system: str, user: str, schema: Any) -> dict[str, Any]:
        self.calls.append(user)
        return self.payload


def _summary(sid: str = "s1") -> SessionSummary:
    return SessionSummary(session_id=sid, goal="add refund", what_failed=["missed changelog"], analyzed_at=T0)


@pytest.mark.unit
def test_propose_rule_deltas_parses_and_skips_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient({"operations": [
        {"op": "add", "section": "Process", "text": "Update CHANGELOG.md", "session_ids": ["s1"]},
        "not a dict",
        {"op": "helpful", "rule_id": "R1"},
    ]})
    monkeypatch.setattr(synthesize, "get_client", lambda billing=None: fake)

    deltas = synthesize.propose_rule_deltas([_summary()], "(no rules yet)", billing="subscription")

    assert [d.op for d in deltas] == ["add", "helpful"]
    assert deltas[0].session_ids == ("s1",)
    assert "missed changelog" in fake.calls[0]


@pytest.mark.integration
def test_run_with_delta_synthesis_saves_rulebook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Provider:
        name = "claude_code"
        display_name = "Claude Code"

        def find_new_sessions(self, cwd: Path) -> list[Path]:
            return [Path("s1.jsonl")]

        def session_id_from_path(self, path: Path) -> str:
            return "s1"

        def parse_session_file(self, path: Path):  # noqa: ANN201
            msg = RawMessage(type="user", role="user", content="add refund")
            return [msg], [{"type": "user"}]

        def expected_location(self, cwd: Path) -> str:
            return "nowhere"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_provider", lambda name=None: _Provider())
    monkeypatch.setattr(cli, "analyze_session", lambda *a, **k: _summary())
    monkeypatch.setattr(
        cli, "propose_rule_deltas",
        lambda *a, **k: [RuleDelta(op="add", section="Process", text="Update CHANGELOG.md", session_ids=("s1",))],
    )

    result = CliRunner().invoke(cli.app, ["run", "--synthesis", "delta", "--billing", "subscription"])

    assert result.exit_code == 0, result.output
    assert "+1 added" in result.output
    assert store.load_rulebook().rules[0].text == "Update CHANGELOG.md"
    assert "Update CHANGELOG.md <!-- R1 -->" in (tmp_path / "SKILL.md").read_text()


@pytest.mark.integration
def test_run_rejects_unknown_synthesis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["run", "--synthesis", "magic"])

    assert result.exit_code == 1
    assert "--synthesis must be one of" in result.output


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["rewrite", "delta"])
def test_learn_skill_from_train_transcripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "add tax"}}) + "\n")
    results = [
        BenchRunResult(
            task_id="tax", split="train", arm="none", seed=0, passed=False, first_attempt_passed=False,
            attempts=[AgentRunStats()], transcript_path=str(transcript), started_at=T0, finished_at=T0,
        ),
        BenchRunResult(
            task_id="refund", split="test", arm="none", seed=0, passed=True, first_attempt_passed=True,
            transcript_path=str(transcript), started_at=T0, finished_at=T0,
        ),
    ]
    seen: list[str] = []

    def fake_analyze(messages, session_id, raw, billing=None):  # noqa: ANN001, ANN202
        assert billing == "subscription"
        seen.append(session_id)
        return _summary(session_id)

    monkeypatch.setattr(learn, "analyze_session", fake_analyze)
    monkeypatch.setattr(learn, "generate_skill_file", lambda *a, **k: ("# Skill\n\n- rewrite rule\n", ""))
    monkeypatch.setattr(
        learn, "propose_rule_deltas", lambda *a, **k: [RuleDelta(op="add", section="P", text="delta rule")]
    )

    content, summaries = learn.learn_skill(results, workers=1, synthesis=mode)

    assert seen == ["bench-tax-none-0"]  # test split never leaks into learning
    assert ("rewrite rule" if mode == "rewrite" else "delta rule") in content
    assert len(summaries) == 1


@pytest.mark.unit
def test_learn_skill_requires_train_results() -> None:
    with pytest.raises(ValueError, match="No train-split"):
        learn.learn_skill([])
