"""Unified model client abstraction for SkillRunes.

Two execution paths, selected at call time based on environment:

  SDK path (ANTHROPIC_API_KEY present in environment):
    Uses anthropic.Anthropic() directly.
    extract_structured() → tool_use with forced tool_choice.
    generate_text()      → messages.create, returns text content.

  Subprocess path (no ANTHROPIC_API_KEY):
    Invokes `claude --print` using Claude Code's own authentication
    (OAuth / keychain — whatever Claude Code is logged in as).
    extract_structured() → --json-schema flag for structured output.
    generate_text()      → plain text output.
    Both paths show a Rich spinner since output is not streamed.

All modules that need model calls must call get_client() from here and use the
returned ModelClient.  Never instantiate anthropic.Anthropic() or subprocess.run
(['claude', ...]) directly from extract.py or synthesize.py.
"""

import json
import os
import shutil
import subprocess
from typing import Any

import anthropic
from anthropic.types import TextBlock, ToolParam
from dotenv import load_dotenv

from skillrunes.config import MODEL

load_dotenv()

_SUBPROCESS_INSTALL_HINT = (
    "Claude Code not found in PATH. "
    "Install it via: npm install -g @anthropic-ai/claude-code"
)

# Subprocess timeout in seconds — generous for large sessions
_SUBPROCESS_TIMEOUT = 120


class ModelClient:
    """Wraps SDK or subprocess path behind a single interface.

    Instantiate via get_client() rather than directly, so callers are
    decoupled from the selection logic.
    """

    def __init__(self, *, sdk_client: anthropic.Anthropic | None = None) -> None:
        self._sdk = sdk_client

    @property
    def mode(self) -> str:
        return "sdk" if self._sdk is not None else "subprocess"

    # ------------------------------------------------------------------
    # Structured extraction — used by extract.py
    # ------------------------------------------------------------------

    def extract_structured(
        self,
        system: str,
        user: str,
        tool_schema: ToolParam,
    ) -> dict[str, Any]:
        """Return a dict matching tool_schema by forcing structured output.

        SDK path:  uses tool_use with tool_choice={"type":"tool","name":"..."}.
        Subprocess: uses --json-schema with the schema's input_schema section.
        """
        if self._sdk is not None:
            return self._sdk_extract(system, user, tool_schema)
        return self._subprocess_extract(system, user, tool_schema)

    # ------------------------------------------------------------------
    # Free-form text generation — used by synthesize.py
    # ------------------------------------------------------------------

    def generate_text(self, system: str, user: str) -> str:
        """Return a plain text response.

        SDK path:  messages.create, joins text content blocks.
        Subprocess: --print with default text output format.
        """
        if self._sdk is not None:
            return self._sdk_generate(system, user)
        return self._subprocess_generate(system, user)

    # ------------------------------------------------------------------
    # SDK implementations
    # ------------------------------------------------------------------

    def _sdk_extract(
        self,
        system: str,
        user: str,
        tool_schema: ToolParam,
    ) -> dict[str, Any]:
        assert self._sdk is not None
        tool_name: str = tool_schema["name"]  # type: ignore[index]
        response = self._sdk.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input  # type: ignore[return-value]
        raise ValueError(
            f"Model did not return a {tool_name!r} tool call. "
            f"stop_reason={response.stop_reason!r}"
        )

    def _sdk_generate(self, system: str, user: str) -> str:
        assert self._sdk is not None
        response = self._sdk.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = [
            block.text
            for block in response.content
            if isinstance(block, TextBlock)
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Subprocess implementations
    # ------------------------------------------------------------------

    def _subprocess_extract(
        self,
        system: str,
        user: str,
        tool_schema: ToolParam,
    ) -> dict[str, Any]:
        """Call claude CLI for structured JSON extraction.

        --system-prompt combined with --json-schema silently returns empty output
        in some environments.  Instead we embed the system instructions and schema
        directly into the user message so only the base --print flag is needed.
        The model is instructed to respond with ONLY a raw JSON object.
        """
        input_schema = tool_schema.get("input_schema", {})  # type: ignore[union-attr]
        schema_str = json.dumps(input_schema, indent=2)
        combined_input = (
            f"<instructions>\n{system}\n</instructions>\n\n"
            f"<json_schema>\n{schema_str}\n</json_schema>\n\n"
            "IMPORTANT: Respond ONLY with a valid JSON object matching the schema "
            "above. No prose, no markdown fences, no explanation.\n\n"
            f"<session_transcript>\n{user}\n</session_transcript>"
        )

        cmd = ["claude", "--print", "--no-session-persistence"]
        raw = self._run_subprocess(cmd, stdin_input=combined_input)
        raw = _strip_markdown_fences(raw)

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"claude subprocess returned non-JSON output for extraction: {exc}\n"
                f"Output was: {raw[:300]}"
            ) from exc

    def _subprocess_generate(self, system: str, user: str) -> str:
        """Call claude CLI for free-form text generation.

        --system-prompt works reliably here because we do NOT combine it with
        --json-schema (that combination produces empty output).  The user message
        is passed via stdin rather than as a positional argument.
        """
        cmd = ["claude", "--print", "--no-session-persistence", "--system-prompt", system]
        return self._run_subprocess(cmd, stdin_input=user)

    def _run_subprocess(
        self, cmd: list[str], stdin_input: str | None = None
    ) -> str:
        """Run a claude CLI command and return stdout text.

        User input is passed via stdin rather than as a positional argument —
        the claude CLI silently returns empty output when --system-prompt is long
        and the prompt is a positional argument; stdin always works.

        Claude Code injects CLAUDECODE=1 and related env vars that cause child
        claude processes to detect nesting and return empty output.  We strip
        those vars so the subprocess runs as a fresh non-nested call.
        """
        _require_claude_in_path()

        clean_env = _subprocess_env()

        try:
            result = subprocess.run(
                cmd,
                input=stdin_input or "",
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
                env=clean_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"claude subprocess timed out after {_SUBPROCESS_TIMEOUT}s"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"claude subprocess exited with code {result.returncode}.\n"
                f"stderr: {result.stderr.strip()[:500]}"
            )

        return result.stdout.strip()


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _require_claude_in_path() -> None:
    if shutil.which("claude") is None:
        raise EnvironmentError(_SUBPROCESS_INSTALL_HINT)


_CLAUDE_CODE_ENV_PREFIXES = (
    "CLAUDECODE",
    "CLAUDE_CODE_",
    "CLAUDE_EFFORT",
    "AI_AGENT",
)


def _subprocess_env() -> dict[str, str]:
    """Return os.environ with Claude Code nesting-detection vars stripped.

    When skillrunes is run from inside Claude Code, CLAUDECODE=1 and related
    vars cause a child `claude --print` process to detect nesting and return
    empty output.  Clearing them lets the subprocess run as a fresh session.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not any(k.startswith(prefix) for prefix in _CLAUDE_CODE_ENV_PREFIXES)
    }


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers from model output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Drop first line (``` or ```json) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        return "\n".join(inner).strip()
    return stripped


def get_client() -> ModelClient:
    """Return a ModelClient using the best available auth method.

    Prefers the SDK (ANTHROPIC_API_KEY) for reliability and full feature
    support.  Falls back to the claude CLI subprocess if no key is set.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        sdk = anthropic.Anthropic(api_key=api_key)
        return ModelClient(sdk_client=sdk)

    # No API key — fall back to Claude Code CLI subprocess
    _require_claude_in_path()  # fail fast with a clear message
    return ModelClient(sdk_client=None)


# Convenience alias for callers that prefer the product-specific name.
SkillRunesClient = ModelClient
