"""Provider guidance never executes login or reads account credentials."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nexus.core import provider_account_commands as commands
from nexus.core import studio_desktop_accounts as accounts
from nexus.core.studio_desktop_cli import add_parser, desktop_command


@pytest.mark.parametrize(
    "provider,executable,args",
    [
        ("codex", "codex", ["login", "--device-auth"]),
        ("claude", "claude", ["auth", "login"]),
        ("agy", "agy", []),
    ],
)
def test_guidance_reuses_provider_command(monkeypatch, provider, executable, args):
    monkeypatch.setattr(commands.shutil, "which", lambda name: "/fixture/" + name)
    assert accounts.provider_login_setup(provider)["command"] == ["/fixture/" + executable, *args]


def test_unknown_provider_is_not_an_arbitrary_command():
    with pytest.raises(ValueError):
        accounts.provider_login_setup("sh -c anything")


def test_missing_cli_reports_setup_needed_without_api_fallback(monkeypatch):
    monkeypatch.setattr(commands.shutil, "which", lambda name: None)
    monkeypatch.setattr(commands.Path, "is_file", lambda path: False)
    result = accounts.provider_login_setup("codex")
    assert result["installed"] is False and result["command"] == []


def test_custom_arguments_cannot_copy_secrets_into_terminal_launcher(monkeypatch):
    monkeypatch.setattr(
        accounts,
        "_provider_account_login_command",
        lambda name: ["codex", "login", "--key", "synthetic"],
    )
    with pytest.raises(ValueError, match="manually"):
        accounts.provider_login_setup("codex")


def test_cli_returns_guidance_without_launching_provider(monkeypatch, capsys):
    monkeypatch.setattr(commands.shutil, "which", lambda name: "/fixture/" + name)
    parser = argparse.ArgumentParser()
    add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["desktop", "account-provider", "--provider", "claude"])
    assert desktop_command(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["label"] == "Claude Code" and value["installed"] is True


def test_local_guidance_imports_without_server_project_configuration(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nexus.core.studio_desktop_accounts import provider_login_setup; import json; print(json.dumps(provider_login_setup('codex')))",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        env={"HOME": str(tmp_path), "PATH": os.defpath, "PYTHONPATH": str(root)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["provider"] == "codex"
