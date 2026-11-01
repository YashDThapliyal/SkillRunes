import json
import subprocess
from typing import Any

import pytest

import skillrunes.billing as billing
import skillrunes.client as client
from skillrunes.billing import (
    BillingError,
    require_subscription_auth,
    resolve_billing_mode,
    subscription_env,
)


@pytest.mark.unit
def test_subscription_env_strips_api_and_nesting_vars() -> None:
    base = {
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "ANTHROPIC_AUTH_TOKEN": "tok",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "PATH": "/usr/bin",
        "HOME": "/home/me",
    }

    env = subscription_env(base)

    assert env == {"PATH": "/usr/bin", "HOME": "/home/me"}
    assert "ANTHROPIC_API_KEY" in base  # input is not mutated


@pytest.mark.unit
def test_resolve_billing_mode_prefers_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILLRUNES_BILLING", "api")

    assert resolve_billing_mode("subscription") == "subscription"
    assert resolve_billing_mode() == "api"


@pytest.mark.unit
def test_resolve_billing_mode_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown billing mode"):
        resolve_billing_mode("free")


@pytest.mark.unit
def test_subscription_mode_ignores_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(client, "_require_claude_in_path", lambda: None)

    assert client.get_client("subscription").mode == "subprocess"
    assert client.get_client("auto").mode == "sdk"


@pytest.mark.unit
def test_api_mode_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(EnvironmentError, match="requires ANTHROPIC_API_KEY"):
        client.get_client("api")


@pytest.mark.unit
def test_client_subprocess_env_never_carries_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    assert "ANTHROPIC_API_KEY" not in client._subprocess_env()


def _fake_run(payload: dict[str, Any]) -> Any:
    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(payload), stderr="")

    return run


@pytest.mark.unit
def test_require_subscription_auth_accepts_claude_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(billing.shutil, "which", lambda _: "/bin/claude")
    monkeypatch.setattr(billing.subprocess, "run", _fake_run({"loggedIn": True, "authMethod": "claude.ai"}))

    require_subscription_auth()


@pytest.mark.unit
def test_require_subscription_auth_rejects_api_key_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(billing.shutil, "which", lambda _: "/bin/claude")
    monkeypatch.setattr(billing.subprocess, "run", _fake_run({"loggedIn": True, "authMethod": "api_key"}))

    with pytest.raises(BillingError, match="api_key"):
        require_subscription_auth()


@pytest.mark.unit
def test_require_subscription_auth_rejects_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(billing.shutil, "which", lambda _: "/bin/claude")
    monkeypatch.setattr(billing.subprocess, "run", _fake_run({"loggedIn": False}))

    with pytest.raises(BillingError, match="none"):
        require_subscription_auth()
