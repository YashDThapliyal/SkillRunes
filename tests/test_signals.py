from typing import Any

import pytest

from skillrunes.extract import render_transcript
from skillrunes.models import RawMessage
from skillrunes.signals import compute_signals, is_correction


def _assistant(*calls: tuple[str, str, dict[str, Any]]) -> RawMessage:
    blocks = [
        {"type": "tool_use", "id": call_id, "name": name, "input": args}
        for call_id, name, args in calls
    ]
    msg = RawMessage.from_jsonl_line(
        {"type": "assistant", "message": {"role": "assistant", "content": blocks}}
    )
    assert msg is not None
    return msg


def _result(call_id: str, is_error: bool, content: Any = "ok") -> RawMessage:
    block = {"type": "tool_result", "tool_use_id": call_id, "is_error": is_error, "content": content}
    msg = RawMessage.from_jsonl_line({"type": "user", "message": {"role": "user", "content": [block]}})
    assert msg is not None
    return msg


def _user(text: str) -> RawMessage:
    msg = RawMessage.from_jsonl_line({"type": "user", "message": {"role": "user", "content": text}})
    assert msg is not None
    return msg


@pytest.mark.unit
def test_tool_result_blocks_are_parsed_with_error_flag() -> None:
    msg = _result("t1", True, [{"type": "text", "text": "Exit code 1\nboom"}])

    assert len(msg.tool_results) == 1
    assert msg.tool_results[0].is_error is True
    assert msg.tool_results[0].content.startswith("Exit code 1")


@pytest.mark.unit
def test_tool_result_content_is_truncated() -> None:
    msg = _result("t1", True, "x" * 5000)

    assert len(msg.tool_results[0].content) == 500


@pytest.mark.unit
def test_signals_count_errors_by_tool() -> None:
    messages = [
        _user("fix the tests"),
        _assistant(("a", "Bash", {"command": "pytest"}), ("b", "Read", {"file_path": "x"})),
        _result("a", True),
        _result("b", False),
    ]

    signals = compute_signals(messages)

    assert signals.tool_calls == 2
    assert signals.tool_errors == 1
    assert signals.tool_error_rate == 0.5
    assert signals.errors_by_tool == {"Bash": 1}


@pytest.mark.unit
def test_retry_loop_is_identical_call_after_failure() -> None:
    messages = [
        _assistant(("a", "Bash", {"command": "npm test"})),
        _result("a", True),
        _assistant(("b", "Bash", {"command": "npm test"})),
        _result("b", True),
        _assistant(("c", "Bash", {"command": "npm test"})),
        _result("c", False),
        _assistant(("d", "Bash", {"command": "npm run build"})),
    ]

    assert compute_signals(messages).retry_loops == 2


@pytest.mark.unit
def test_repeated_successful_call_is_not_a_retry() -> None:
    messages = [
        _assistant(("a", "Bash", {"command": "git status"})),
        _result("a", False),
        _assistant(("b", "Bash", {"command": "git status"})),
    ]

    assert compute_signals(messages).retry_loops == 0


@pytest.mark.unit
def test_interruptions_and_corrections_are_separate() -> None:
    messages = [
        _user("add a login page"),
        _user("[Request interrupted by user for tool use]"),
        _user("no, use the existing auth module"),
        _user("looks good, ship it"),
    ]

    signals = compute_signals(messages)

    assert signals.interruptions == 1
    assert signals.user_corrections == 1
    assert signals.user_turns == 3


@pytest.mark.unit
def test_first_user_turn_is_never_a_correction() -> None:
    messages = [_user("stop the dashboard server and restart it")]

    assert compute_signals(messages).user_corrections == 0


@pytest.mark.unit
def test_system_injected_user_text_is_ignored() -> None:
    messages = [
        _user("build it"),
        _user("<system-reminder>no tools allowed</system-reminder>"),
        _user("Stop hook feedback: [revert this]"),
    ]

    signals = compute_signals(messages)

    assert signals.user_turns == 1
    assert signals.user_corrections == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("no that's wrong", True),
        ("why did you delete the migration", True),
        ("revert that change", True),
        ("that's not what I asked for", True),
        ("great, now add tests", False),
        ("can you also update the README", False),
        ("don't forget the changelog", False),
    ],
)
def test_is_correction(text: str, expected: bool) -> None:
    assert is_correction(text) is expected


@pytest.mark.unit
def test_empty_session_has_zero_rates() -> None:
    signals = compute_signals([])

    assert signals.tool_error_rate == 0.0
    assert signals.correction_rate == 0.0


@pytest.mark.unit
def test_render_transcript_includes_tool_errors() -> None:
    messages = [_user("run tests"), _result("a", True, "ModuleNotFoundError: foo")]

    rendered = render_transcript(messages)

    assert "[TOOL ERROR]\nModuleNotFoundError: foo" in rendered
