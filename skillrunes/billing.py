"""Billing-mode helpers for anything that launches the ``claude`` CLI.

Claude Code prefers ANTHROPIC_API_KEY over the logged-in subscription when the
variable is present.  A user with a key exported in their shell would therefore
silently bill API credits for every ``claude --print`` call SkillRunes makes.

These helpers build a scrubbed environment so CLI subprocesses always use the
logged-in Claude subscription, and verify that login before expensive work
such as benchmark runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from skillrunes.config import BILLING_ENV_VAR, BILLING_MODES, DEFAULT_BILLING

# Variables that make a child `claude` bill an API account instead of the
# subscription.  Bedrock/Vertex switches are included so a stray shell export
# cannot reroute benchmark traffic.
_API_BILLING_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

# Variables Claude Code sets in its own process tree.  A child `claude`
# that inherits them detects nesting and can return empty output.
_NESTING_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_EFFORT", "AI_AGENT")

SUBSCRIPTION_AUTH_METHOD = "claude.ai"
_AUTH_STATUS_TIMEOUT = 30


class BillingError(RuntimeError):
    """Raised when the CLI is not authenticated the way the caller requires."""


def resolve_billing_mode(mode: str | None = None) -> str:
    """Return the billing mode from the argument, env var, or default."""
    selected = (mode or os.environ.get(BILLING_ENV_VAR) or DEFAULT_BILLING).strip().lower()
    if selected not in BILLING_MODES:
        raise ValueError(
            f"Unknown billing mode {selected!r}. Choose one of: {', '.join(BILLING_MODES)}."
        )
    return selected


# Subscription credentials that must survive the nesting-prefix scrub.
_SUBSCRIPTION_VARS = ("CLAUDE_CODE_OAUTH_TOKEN",)


def subscription_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``base`` (default: os.environ) safe for subscription CLI calls.

    API-billing variables and Claude Code nesting markers are removed.  The
    input mapping is never mutated.
    """
    source = dict(os.environ if base is None else base)
    return {
        key: value
        for key, value in source.items()
        if key not in _API_BILLING_VARS
        and (
            key in _SUBSCRIPTION_VARS
            or not any(key.startswith(prefix) for prefix in _NESTING_PREFIXES)
        )
    }


def cli_auth_method(env: dict[str, str] | None = None) -> str:
    """Return the auth method reported by ``claude auth status`` ("" if unknown)."""
    if shutil.which("claude") is None:
        raise BillingError("Claude Code CLI not found in PATH.")
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=_AUTH_STATUS_TIMEOUT,
            env=env if env is not None else subscription_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise BillingError("`claude auth status` timed out.") from exc
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    if not payload.get("loggedIn"):
        return ""
    return str(payload.get("authMethod", ""))


def require_subscription_auth() -> None:
    """Fail fast unless the scrubbed CLI environment uses a Claude subscription."""
    method = cli_auth_method(subscription_env())
    if method != SUBSCRIPTION_AUTH_METHOD:
        raise BillingError(
            "Claude Code is not logged in with a Claude subscription "
            f"(auth method: {method or 'none'}). Run `claude auth login` first."
        )
