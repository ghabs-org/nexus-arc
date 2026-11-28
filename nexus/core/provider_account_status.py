"""Local CLI-reported authentication, without model calls or credential-file reads."""

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

STATUS_COMMANDS = {
    "codex": ("login", "status"),
    "claude-code": ("auth", "status", "--json"),
    "agy": ("models",),
}


def provider_account_status(provider: str, executable: str | None, *, env=None, home=None, timeout=4) -> dict:
    """Return sanitized status only; never return CLI output or account identifiers.

    These commands report local credentials. They do not establish remaining
    quota or guarantee that a remote provider will accept the next model request.
    """
    result = {
        "state": "unknown",
        "method": None,
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "local-cli",
        "message": "Couldn’t verify sign-in. Refresh status or update the CLI.",
    }
    if not executable:
        return {**result, "state": "not-installed", "message": "Install the official CLI first."}
    if provider not in STATUS_COMMANDS:
        return {**result, "message": "This CLI has no supported sign-in status check."}
    try:
        process = subprocess.run(
            [executable, *STATUS_COMMANDS[provider]],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=home or str(Path.home()),
            env=env,
        )
        if len(process.stdout) + len(process.stderr) > 65536:
            return result
        if provider == "claude-code":
            data = json.loads(process.stdout)
            if not isinstance(data, dict):
                return result
            if data.get("loggedIn") is True and process.returncode == 0:
                method = data.get("authMethod")
                method = (
                    "subscription" if method == "claude.ai"
                    else "api" if method in {"api_key", "api_key_helper"}
                    else "oauth" if method == "oauth_token"
                    else "other"
                )
                return {**result, "state": "signed-in", "method": method,
                        "message": "The CLI reports signed-in credentials. Usage limits apply."}
            if data.get("loggedIn") is False and process.returncode in {0, 1}:
                return {**result, "state": "signed-out", "message": "Sign in with the provider’s CLI."}
        elif provider == "agy":
            # `agy models` returns tab-separated IDs/display names after account
            # access succeeds. A zero exit with help/empty output is not proof.
            output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", process.stdout + "\n" + process.stderr)
            if re.search(r"\b(not (?:logged|signed) in|not authenticated|authentication required|please (?:sign|log) in)\b", output, re.I):
                return {**result, "state": "signed-out", "message": "Sign in with the provider’s CLI."}
            rows = [line.strip().split("\t", 1) for line in output.splitlines()]
            catalog = any(
                len(row) == 2 and row[1].strip()
                and row[0] not in {"id", "model", "model_id", "name"}
                and re.fullmatch(r"[a-z0-9][a-z0-9._:/+-]*", row[0])
                for row in rows
            )
            if process.returncode == 0 and catalog and not re.search(r"^\s*error\s*:", output, re.I | re.M):
                return {**result, "state": "signed-in", "method": "other",
                        "message": "The CLI successfully listed available models. Usage limits apply."}
        else:
            # Some Codex versions write status (including API-key hints) to stderr.
            # Interpret known phrases only; never copy either stream to the client.
            output = (process.stdout + "\n" + process.stderr).lower()
            if process.returncode == 0:
                method = (
                    "subscription" if "logged in using chatgpt" in output
                    else "api" if "logged in using an api key" in output
                    else None
                )
                if method:
                    return {**result, "state": "signed-in", "method": method,
                            "message": "The CLI reports signed-in credentials. Usage limits apply."}
            elif process.returncode == 1 and "not logged in" in output:
                return {**result, "state": "signed-out", "message": "Sign in with the provider’s CLI."}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        pass
    return result
