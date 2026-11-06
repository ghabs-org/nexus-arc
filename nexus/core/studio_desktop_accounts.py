"""Local provider login guidance shared by desktop shells and the CLI.

This describes a login command only. The user starts it explicitly in a terminal;
provider CLIs keep their own credentials and no account data crosses to a VM.
"""

import os
import shlex
import shutil
from pathlib import Path

from nexus.core.provider_account_commands import (
    DEFAULT_ARGS as _PROVIDER_ACCOUNT_CONNECT_DEFAULT_ARGS,
)
from nexus.core.provider_account_commands import (
    provider_account_login_command as _provider_account_login_command,
)


def provider_login_setup(provider: str) -> dict:
    labels = {"codex": "Codex", "claude": "Claude Code", "agy": "Antigravity"}
    if provider not in labels:
        raise ValueError("Choose Codex, Claude Code or Antigravity.")
    command = _provider_account_login_command(provider)
    # Custom headless/server arguments can contain secrets or non-login actions.
    # Never copy them into a terminal launcher; keep desktop guidance to login.
    if command[1:] != shlex.split(_PROVIDER_ACCOUNT_CONNECT_DEFAULT_ARGS[provider]):
        raise ValueError("Run custom provider login arguments manually in your terminal.")
    executable = shutil.which(command[0])
    if not executable:
        for folder in [
            Path.home() / ".local/bin",
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
        ]:
            candidate = folder / command[0]
            if candidate.is_file() and os.access(candidate, os.X_OK):
                executable = str(candidate.resolve())
                break
    return {
        "provider": provider,
        "label": labels[provider],
        "installed": bool(executable),
        "command": [executable, *command[1:]] if executable else [],
        "notice": "Opens this provider's local sign-in. Credentials remain managed by its CLI. "
        "Installation does not prove authentication. Provider subscription terms apply.",
    }
