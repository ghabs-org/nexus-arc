"""Finder's short PATH must not disagree with setup or switch an explicit CLI."""

import asyncio

import pytest

from nexus.adapters.registry import AdapterRegistry
from nexus.core.provider_account_commands import resolve_provider_cli
from nexus.core.studio_desktop_accounts import provider_login_setup


@pytest.mark.parametrize(
    "provider,binary,login_id", [("claude-code", "claude", "claude"), ("codex", "codex", "codex"), ("agy", "agy", "agy")]
)
def test_finder_path_runs_the_discovered_executable(tmp_path, monkeypatch, provider, binary, login_id):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    executable = tmp_path / ".local/bin" / binary
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\n[ "$1" = "--version" ] || exit 9\nprintf "fixture 1.0\\n"\n')
    executable.chmod(0o700)
    adapter = AdapterRegistry().create_ai(provider)
    assert asyncio.run(adapter.check_availability()) is True
    assert adapter._binary == str(executable)
    assert provider_login_setup(login_id)["command"][0] == str(executable)


def test_explicit_missing_path_never_uses_another_installation(tmp_path, monkeypatch):
    monkeypatch.setattr("nexus.core.provider_account_commands.shutil.which", lambda _: None)
    # Even if a fallback location contains an executable, an explicit path is binding.
    monkeypatch.setattr("nexus.core.provider_account_commands.Path.is_file", lambda _: True)
    monkeypatch.setattr("nexus.core.provider_account_commands.os.access", lambda *_: True)
    assert resolve_provider_cli(str(tmp_path / "missing/claude")) is None


def test_discovery_preserves_path_precedence(monkeypatch):
    monkeypatch.setattr("nexus.core.provider_account_commands.shutil.which", lambda _: "/chosen/claude")
    assert resolve_provider_cli("claude") == "/chosen/claude"


def test_installed_but_unrunnable_cli_fails_readiness(tmp_path, monkeypatch):
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\nexit 9\n")
    executable.chmod(0o700)
    adapter = AdapterRegistry().create_ai("claude-code", binary=str(executable))
    assert asyncio.run(adapter.check_availability()) is False
