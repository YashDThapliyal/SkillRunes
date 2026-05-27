from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from typer.testing import CliRunner

import skillrunes.cli as cli
from skillrunes.ingest import get_provider, resolve_provider_name
from skillrunes.ingest.base import ProviderNotImplementedError, TranscriptProvider
from skillrunes.ingest.claude_code import ClaudeCodeProvider
from skillrunes.ingest.codex import CODEX_NOT_IMPLEMENTED_MESSAGE, CodexProvider


def test_provider_selection_defaults_to_claude_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKILLRUNES_PROVIDER", raising=False)

    provider = get_provider()

    assert provider.name == "claude_code"
    assert provider.display_name == "Claude Code"


def test_provider_selection_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILLRUNES_PROVIDER", "codex")

    provider = get_provider()

    assert provider.name == "codex"


def test_provider_selection_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown SkillRunes provider"):
        resolve_provider_name("unknown")


def test_claude_code_provider_parses_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "mock_session.jsonl"
    provider = ClaudeCodeProvider()

    messages, raw_lines = provider.parse_session_file(fixture)

    assert provider.session_id_from_path(fixture) == "mock_session"
    assert len(raw_lines) == 17
    assert len(messages) == 15
    assert messages[0].role == "user"
    assert messages[0].content.startswith("I need to add pagination")
    assert messages[1].tool_calls[0].name == "Read"


def test_codex_provider_raises_clear_error() -> None:
    provider = CodexProvider()

    with pytest.raises(ProviderNotImplementedError, match="Codex transcript ingestion"):
        provider.find_session_files(Path.cwd())

    with pytest.raises(ProviderNotImplementedError) as exc_info:
        provider.parse_session_file(Path("x"))
    assert CODEX_NOT_IMPLEMENTED_MESSAGE in str(exc_info.value)


class _NoSessionsProvider(TranscriptProvider):
    name = "fake"
    display_name = "Fake Provider"

    def find_session_files(self, cwd: Path) -> list[Path]:
        return []

    def parse_session_file(self, path: Path) -> tuple[list, list[dict]]:
        return [], []

    def session_id_from_path(self, path: Path) -> str:
        return path.stem


def test_cli_displays_active_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "get_provider", lambda provider_name=None: _NoSessionsProvider())

    with TemporaryDirectory() as tmpdir:
        monkeypatch.chdir(tmpdir)
        result = runner.invoke(cli.app, ["run", "--provider", "claude_code"])

    assert result.exit_code == 0
    assert "Active provider:" in result.output
    assert "Fake Provider" in result.output


def test_codex_provider_does_not_silently_return_zero_sessions() -> None:
    provider = CodexProvider()

    with pytest.raises(ProviderNotImplementedError, match=CODEX_NOT_IMPLEMENTED_MESSAGE):
        provider.find_new_sessions(Path.cwd())
