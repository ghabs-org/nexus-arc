"""Provider sign-in command configuration without server bootstrap or credentials."""

import os
import shlex

CLI_ENV = {"codex": "CODEX_CLI_PATH", "claude": "CLAUDE_CLI_PATH", "agy": "AGY_CLI_PATH"}
ARGS_ENV = {
    "codex": "NEXUS_CODEX_ACCOUNT_CONNECT_ARGS",
    "claude": "NEXUS_CLAUDE_ACCOUNT_CONNECT_ARGS",
    "agy": "NEXUS_AGY_ACCOUNT_CONNECT_ARGS",
}
DEFAULT_ARGS = {"codex": "login --device-auth", "claude": "auth login", "agy": ""}


def provider_account_login_command(provider: str) -> list[str]:
    """Resolve the existing CLI settings without loading auth/project services."""
    cli_env_name = str(CLI_ENV.get(provider) or "").strip()
    args_env_name = str(ARGS_ENV.get(provider) or "").strip()
    default_args = str(DEFAULT_ARGS.get(provider) or "").strip()
    cli_path = str(os.getenv(cli_env_name, provider)).strip() or provider
    args_raw = str(os.getenv(args_env_name, default_args)).strip() or default_args
    try:
        args = shlex.split(args_raw) if args_raw else []
    except ValueError as exc:
        raise ValueError(f"Invalid {args_env_name}: {exc}") from exc
    return [cli_path, *args]
