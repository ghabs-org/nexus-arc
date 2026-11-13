"""Status commands report authentication without starting login or exposing secrets."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from nexus.core import provider_account_status as status


@pytest.mark.parametrize(
    "provider,code,stdout,stderr,state,method",
    [
        ("claude-code", 0, '{"loggedIn":true,"authMethod":"claude.ai"}', "", "signed-in", "subscription"),
        ("claude-code", 0, '{"loggedIn":true,"authMethod":"api_key"}', "", "signed-in", "api"),
        ("claude-code", 1, '{"loggedIn":false,"authMethod":"none"}', "", "signed-out", None),
        ("claude-code", 0, '{"loggedIn":"true"}', "", "unknown", None),
        ("claude-code", 1, '{"loggedIn":true}', "", "unknown", None),
        ("claude-code", 0, "invalid JSON", "", "unknown", None),
        ("codex", 0, "", "Logged in using ChatGPT", "signed-in", "subscription"),
        ("codex", 0, "Logged in using an API key - sk-synthetic-secret", "", "signed-in", "api"),
        ("codex", 1, "", "Not logged in", "signed-out", None),
        ("codex", 2, "", "Could not access account storage", "unknown", None),
        ("codex", 0, "new unsupported status format", "", "unknown", None),
        ("agy", 0, "gemini-fixture\tGemini Fixture\n", "", "signed-in", "other"),
        ("agy", 0, "\x1b[32mgemini-fixture\tGemini Fixture\x1b[0m\n", "", "signed-in", "other"),
        ("agy", 1, "", "Not logged in. Please sign in.", "signed-out", None),
        ("agy", 0, "Please sign in", "", "signed-out", None),
        ("agy", 1, "gemini-fixture\tGemini Fixture\n", "Error: cannot connect", "unknown", None),
        ("agy", 0, "gemini-fixture\tGemini Fixture\n", "Error: cannot connect", "unknown", None),
        ("agy", 0, "", "", "unknown", None),
        ("agy", 0, "Usage: agy models [flags]", "", "unknown", None),
        ("agy", 0, "id\tName\n", "", "unknown", None),
    ],
)
def test_vendor_formats_are_sanitized(monkeypatch, provider, code, stdout, stderr, state, method):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(status.subprocess, "run", run)
    result = status.provider_account_status(provider, "/fixture/cli")
    assert result["state"] == state and result["method"] == method
    assert calls[0][0] == ["/fixture/cli", *status.STATUS_COMMANDS[provider]]
    assert calls[0][1]["timeout"] == 4
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert "shell" not in calls[0][1]
    assert "sk-synthetic-secret" not in json.dumps(result)


def test_private_account_fields_never_leave_status_check(monkeypatch):
    raw = {"loggedIn": True, "authMethod": "claude.ai", "email": "private@example.invalid",
           "orgId": "private-org", "apiKey": "sk-synthetic-private", "configDirectory": "/private/home"}
    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(raw), stderr="private-stderr"))
    result = json.dumps(status.provider_account_status("claude-code", "/fixture/claude"))
    assert "signed-in" in result
    for value in (raw["email"], raw["orgId"], raw["apiKey"], raw["configDirectory"], "private-stderr"):
        assert value not in result


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("cli", 4), OSError("private path")])
def test_probe_failure_is_unknown_not_signed_out(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(status.subprocess, "run", fail)
    assert status.provider_account_status("claude-code", "/fixture/cli")["state"] == "unknown"


def test_missing_and_unsupported_cli_never_start_any_command(monkeypatch):
    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: pytest.fail("Must not start CLI"))
    assert status.provider_account_status("claude-code", None)["state"] == "not-installed"
    assert status.provider_account_status("unsupported", "/fixture/cli")["state"] == "unknown"
