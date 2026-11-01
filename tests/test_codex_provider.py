from pathlib import Path

import pytest

from skillrunes.ingest.codex import CodexProvider
from skillrunes.signals import compute_signals

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "codex"
SESSION_ID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def provider() -> CodexProvider:
    return CodexProvider(roots=(str(FIXTURE_ROOT),))


@pytest.fixture
def fixture_path(provider: CodexProvider) -> Path:
    (path,) = provider.find_session_files(Path("/work/demo"))
    return path


@pytest.mark.integration
def test_find_session_files_matches_cwd_only(provider: CodexProvider) -> None:
    assert len(provider.find_session_files(Path("/work/demo"))) == 1
    assert len(provider.find_session_files(Path("/work/other"))) == 1
    assert provider.find_session_files(Path("/nowhere")) == []


@pytest.mark.integration
def test_missing_root_returns_empty() -> None:
    provider = CodexProvider(roots=("/definitely/not/here",))

    assert provider.find_session_files(Path("/work/demo")) == []


@pytest.mark.integration
def test_session_id_comes_from_filename(provider: CodexProvider, fixture_path: Path) -> None:
    assert provider.session_id_from_path(fixture_path) == SESSION_ID


@pytest.mark.integration
def test_parse_skips_injected_context_and_malformed_lines(
    provider: CodexProvider, fixture_path: Path
) -> None:
    messages, raw_lines = provider.parse_session_file(fixture_path)

    assert len(raw_lines) == 18  # one malformed line skipped
    user_texts = [m.content for m in messages if m.role == "user" and m.content]
    assert user_texts[0] == "Fix the failing date parser test"
    assert not any("environment_context" in text for text in user_texts)
    assert all(m.session_id == SESSION_ID for m in messages)
    assert all(m.cwd == "/work/demo" for m in messages)


@pytest.mark.integration
def test_tool_calls_and_results_are_mapped(provider: CodexProvider, fixture_path: Path) -> None:
    messages, _ = provider.parse_session_file(fixture_path)

    calls = [call for m in messages for call in m.tool_calls]
    results = [result for m in messages for result in m.tool_results]
    assert [c.name for c in calls] == ["shell", "shell", "apply_patch"]
    assert calls[0].input == {"command": ["pytest", "-q"]}
    assert calls[2].input["input"].startswith("*** Begin Patch")
    assert [r.is_error for r in results] == [True, True, False]


@pytest.mark.integration
def test_token_usage_excludes_cached_input(provider: CodexProvider, fixture_path: Path) -> None:
    messages, _ = provider.parse_session_file(fixture_path)

    usages = [m.usage for m in messages if m.usage]
    assert [(u.input_tokens, u.cache_read_input_tokens, u.output_tokens) for u in usages] == [
        (600, 400, 50),
        (500, 1500, 80),
    ]


@pytest.mark.integration
def test_signals_work_on_codex_sessions(provider: CodexProvider, fixture_path: Path) -> None:
    messages, _ = provider.parse_session_file(fixture_path)

    signals = compute_signals(messages)

    assert signals.tool_calls == 3
    assert signals.tool_errors == 2
    assert signals.retry_loops == 1
    assert signals.interruptions == 1
    assert signals.user_corrections == 1
    assert signals.errors_by_tool == {"shell": 2}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("output", "failed"),
    [
        ('{"exit_code":0}', False),
        ('{"exit_code":2}', True),
        ("Exit code: 1\nboom", True),
        ("Script failed\nTypeError", True),
        ("Success. Updated files", False),
        ("Script completed\n" + "x" * 300 + "\nlog line: Exit code: 1", False),
        ("Script completed\n" + "x" * 300 + '\n{"exit_code":3}', True),
    ],
)
def test_failed_output_detection(output: str, failed: bool) -> None:
    from skillrunes.ingest.codex import _is_failed_output

    assert _is_failed_output(output) is failed
