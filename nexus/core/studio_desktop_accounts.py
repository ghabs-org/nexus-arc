"""Local provider login guidance shared by desktop shells and the CLI.

This describes a login command only. The user starts it explicitly in a terminal;
provider CLIs keep their own credentials and no account data crosses to a VM.
"""

import shlex

from nexus.core.provider_account_commands import (
    DEFAULT_ARGS as _PROVIDER_ACCOUNT_CONNECT_DEFAULT_ARGS,
)
from nexus.core.provider_account_commands import (
    provider_account_login_command as _provider_account_login_command,
)
from nexus.core.provider_account_commands import resolve_provider_cli


def provider_login_setup(provider: str) -> dict:
    labels = {"codex": "Codex", "claude": "Claude Code", "agy": "Antigravity"}
    if provider not in labels:
        raise ValueError("Choose Codex, Claude Code or Antigravity.")
    command = _provider_account_login_command(provider)
    # Custom headless/server arguments can contain secrets or non-login actions.
    # Never copy them into a terminal launcher; keep desktop guidance to login.
    if command[1:] != shlex.split(_PROVIDER_ACCOUNT_CONNECT_DEFAULT_ARGS[provider]):
        raise ValueError("Run custom provider login arguments manually in your terminal.")
    executable = resolve_provider_cli(command[0])
    return {
        "provider": provider,
        "label": labels[provider],
        "installed": bool(executable),
        "command": [executable, *command[1:]] if executable else [],
        "notice": "Opens this provider's local sign-in. Credentials remain managed by its CLI. "
        "Installation does not prove authentication. Provider subscription terms apply.",
    }
