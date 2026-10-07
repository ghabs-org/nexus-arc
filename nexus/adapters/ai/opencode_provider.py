"""OpenCode CLI AI provider implementation.

Delegates to the installed ``opencode`` CLI (``opencode run``) using the
caller's account authentication, so Nexus can use account-backed models —
including free tiers — with no API key configuration::

    OpenCodeProvider()  # -> opencode/muse-spark-1.3-contributor-free (free)
    OpenCodeProvider(model="opencode/gpt-5.3-codex-spark")

Override with ``OPENCODE_MODEL`` env var or ``context.model_override``.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.core.models import AgentResult, RateLimitStatus

logger = logging.getLogger(__name__)

# Free-tier default; verified cost=0 via the local OpenCode model catalog.
# ponytail: single pinned default, add config surface if model rotation matters.
DEFAULT_MODEL = "opencode/muse-spark-1.3-contributor-free"


def extract_run_text(raw: str) -> str:
    """Extract assistant text from ``opencode run --format json`` output.

    Falls back to the raw stdout (minus ``>`` status lines) when the output
    is not newline-delimited JSON (e.g. older CLI versions).
    """
    texts: list[str] = []
    is_json = False
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        is_json = True
        part = event.get("part") or {}
        if event.get("type") == "text" and part.get("type") == "text":
            text = str(part.get("text") or "")
            if text:
                texts.append(text)
    if is_json:
        return "\n".join(texts).strip()
    return "\n".join(
        line for line in (raw or "").splitlines() if not line.strip().startswith(">")
    ).strip()


def list_recent_sessions(
    limit: int = 8,
    cli_path: str | None = None,
    timeout: int = 30,
) -> list[dict]:
    """List recent OpenCode server sessions, newest first.

    Calls ``opencode api get /api/session`` (uses the caller's account auth,
    same as ``opencode run``) and normalizes each entry to::

        {"id", "title", "model", "cost", "tokens_in", "tokens_out", "updated_ms"}

    Raises RuntimeError when the CLI call fails.
    """
    binary = (cli_path or os.getenv("OPENCODE_CLI_PATH", "opencode")).strip() or "opencode"
    try:
        result = subprocess.run(
            [binary, "api", "get", "/api/session"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"OpenCode CLI not found: {binary}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OpenCode session list timed out (>{timeout}s)") from exc
    if result.returncode != 0:
        raise RuntimeError(f"OpenCode session list failed: {(result.stderr or '').strip()[:200]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"OpenCode session list returned invalid JSON: {exc}") from exc

    sessions = payload.get("data") or []
    normalized: list[dict] = []
    for entry in sessions:
        if not isinstance(entry, dict):
            continue
        model = entry.get("model") or {}
        tokens = entry.get("tokens") or {}
        normalized.append(
            {
                "id": str(entry.get("id") or ""),
                "title": str(entry.get("title") or "Untitled"),
                "model": f"{model.get('providerID', '?')}/{model.get('modelID') or model.get('id', '?')}",
                "cost": float(entry.get("cost") or 0.0),
                "tokens_in": int(tokens.get("input") or 0),
                "tokens_out": int(tokens.get("output") or 0),
                "updated_ms": int((entry.get("time") or {}).get("updated") or 0),
            }
        )
    normalized.sort(key=lambda item: item["updated_ms"], reverse=True)
    return normalized[: max(int(limit), 1)]


class OpenCodeProvider(AIProvider):
    """AI provider that delegates to the OpenCode CLI (``opencode run``)."""

    def __init__(
        self,
        timeout: int = 600,
        cli_path: str | None = None,
        model: str | None = None,
    ):
        self._timeout = timeout
        self._cli_path = (cli_path or os.getenv("OPENCODE_CLI_PATH", "opencode")).strip() or "opencode"
        self._model = (model or os.getenv("OPENCODE_MODEL", "")).strip() or DEFAULT_MODEL
        self._availability_cache: dict = {}
        self._availability_ttl: int = 300  # seconds

    @property
    def name(self) -> str:
        return "opencode"

    async def check_availability(self) -> bool:
        """Return True if the ``opencode`` CLI binary is installed and runnable."""
        now = time.time()
        cached = self._availability_cache.get("opencode")
        if cached and now - cached["at"] < self._availability_ttl:
            return cached["available"]

        available = bool(shutil.which(self._cli_path))
        if available:
            try:
                result = subprocess.run(
                    [self._cli_path, "--version"],
                    capture_output=True,
                    timeout=10,
                )
                available = result.returncode == 0
            except Exception:
                available = False

        self._availability_cache["opencode"] = {"available": available, "at": now}
        return available

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """OpenCode CLI has no programmatic rate-limit endpoint; report unlimited."""
        return RateLimitStatus(
            provider=self.name,
            is_limited=False,
        )

    def get_preference_score(self, task_type: str) -> float:
        """Return 0.8 for reasoning/analysis/chat tasks, 0.7 otherwise."""
        return 0.8 if task_type in {"reasoning", "analysis", "general_chat"} else 0.7

    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        """Run the agent prompt through ``opencode run --format json``."""
        start = time.time()
        workspace = Path(context.workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        model = (context.model_override or "").strip() or self._model
        prompt = context.prompt
        if context.issue_url:
            prompt = f"{prompt}\n\nIssue: {context.issue_url}"
        cmd = [self._cli_path, "run", "--model", model, "--format", "json", prompt]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=context.timeout or self._timeout,
            )
            elapsed = time.time() - start
            if process.returncode == 0:
                return AgentResult(
                    success=True,
                    output=extract_run_text(stdout.decode(errors="replace")),
                    execution_time=elapsed,
                    provider_used=self.name,
                    metadata={"model": model},
                )
            return AgentResult(
                success=False,
                output=stdout.decode(errors="replace"),
                error=stderr.decode(errors="replace"),
                execution_time=elapsed,
                provider_used=self.name,
            )
        except TimeoutError:
            return AgentResult(
                success=False,
                output="",
                error=f"Timeout after {context.timeout or self._timeout}s",
                execution_time=time.time() - start,
                provider_used=self.name,
            )
        except Exception as exc:
            return AgentResult(
                success=False,
                output="",
                error=str(exc),
                execution_time=time.time() - start,
                provider_used=self.name,
            )
