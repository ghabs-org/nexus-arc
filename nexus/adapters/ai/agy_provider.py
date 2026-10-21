"""Google Antigravity CLI AI provider (`agy -p=`): runs on the local Antigravity login, no API key."""

import asyncio
import json
import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from nexus.adapters.ai.base import AIProvider, ExecutionContext, split_model_effort
from nexus.core.models import AgentResult, RateLimitStatus

logger = logging.getLogger(__name__)

_AGY_PREFERRED_TASKS = {"reasoning", "analysis", "code_generation", "code_review"}


def agy_print_args(timeout: int, model: str | None = None, effort: str | None = None) -> list[str]:
    """Arguments after the binary for one stream-json print-mode turn."""
    args = [
        "-p=",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--disable-slash-commands",
        "--print-timeout",
        f"{timeout}s",
    ]
    if model:
        args.extend(["--model", model])
    if effort:
        args.extend(["--effort", effort])
    return args


def agy_stream_input(prompt: str) -> bytes:
    """The stdin NDJSON line carrying one user turn."""
    return (
        json.dumps({"event": "user", "message": {"role": "user", "content": prompt}}) + "\n"
    ).encode()


def parse_agy_stream_output(
    stdout: bytes,
    returncode: int | None,
    stderr: bytes,
    elapsed: float,
    model: str | None,
    provider_name: str = "agy",
) -> AgentResult:
    """Turn agy stream-json output into an AgentResult (final `result` event wins)."""
    res_payload: dict = {}
    for raw_line in (stdout or b"").splitlines():
        line = raw_line.strip()
        if not line.startswith(b"{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "result" and isinstance(event.get("result"), dict):
            res_payload = event["result"]
        elif event.get("type") == "result":
            res_payload = event

    status = str(res_payload.get("status") or "")
    output = str(res_payload.get("response") or res_payload.get("result") or "")
    is_error = bool(
        returncode != 0
        or res_payload.get("is_error")
        or (status and status != "SUCCESS")
        or not res_payload
    )
    if is_error:
        stdout_text = (stdout or b"").decode(errors="replace").strip()
        stdout_err_lines = [
            line.strip()
            for line in stdout_text.splitlines()
            if line.strip() and not line.strip().startswith("{")
        ]
        stdout_err = "\n".join(stdout_err_lines).strip()
        err_msg = (
            str(res_payload.get("error") or "")
            or output
            or (stderr or b"").decode(errors="replace").strip()
            or stdout_err
            or f"agy exited {returncode}"
        )
        return AgentResult(
            success=False,
            output=output,
            error=err_msg,
            execution_time=elapsed,
            provider_used=provider_name,
        )
    usage = res_payload.get("usage") or {}
    return AgentResult(
        success=True,
        output=output,
        execution_time=elapsed,
        provider_used=provider_name,
        metadata={
            "model": model,
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
        },
    )


class AntigravityProvider(AIProvider):
    """AI provider that delegates to the Google Antigravity CLI (`agy`) in print mode."""

    def __init__(
        self,
        timeout: int = 600,
        binary: str = "agy",
        model: str | None = None,
        effort: str | None = None,
        extra_args: list[str] | None = None,
    ):
        self._timeout = timeout
        self._binary = binary
        self._model = model
        self._effort = effort
        self._extra_args = extra_args or []
        self._availability_cache: dict = {}
        self._availability_ttl: int = 300  # seconds

    @property
    def name(self) -> str:
        return "agy"

    async def check_availability(self) -> bool:
        """Return True if the agy CLI binary is installed and runnable."""
        now = time.time()
        cached = self._availability_cache.get("agy")
        if cached and now - cached["at"] < self._availability_ttl:
            return cached["available"]

        available = bool(shutil.which(self._binary))
        if available:
            try:
                result = subprocess.run([self._binary, "--version"], capture_output=True, timeout=10)
                available = result.returncode == 0
            except Exception:
                available = False

        self._availability_cache["agy"] = {"available": available, "at": now}
        return available

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Subscription limits are not exposed programmatically; report unlimited."""
        return RateLimitStatus(provider=self.name, is_limited=False)

    def get_preference_score(self, task_type: str) -> float:
        """Return 0.9 for reasoning/coding tasks, 0.75 otherwise."""
        return 0.9 if task_type in _AGY_PREFERRED_TASKS else 0.75

    def _cmd(self, context: ExecutionContext) -> tuple[list[str], str | None, str | None]:
        timeout = context.timeout or self._timeout
        model, effort = split_model_effort(
            context.model_override or self._model,
            context.effort or self._effort,
        )
        cmd = [self._binary, *agy_print_args(timeout, model, effort), *self._extra_args]
        return cmd, model, effort

    def _failure(self, error: str, start: float) -> AgentResult:
        return AgentResult(
            success=False,
            output="",
            error=error,
            execution_time=time.time() - start,
            provider_used=self.name,
        )

    def _parse_stream_output(
        self,
        stdout: bytes,
        returncode: int | None,
        stderr: bytes,
        elapsed: float,
        model: str | None,
    ) -> AgentResult:
        return parse_agy_stream_output(stdout, returncode, stderr, elapsed, model, self.name)

    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        """Run the prompt through `agy -p= --input-format stream-json --output-format stream-json`."""
        start = time.time()
        workspace = Path(context.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        cmd, model, _ = self._cmd(context)
        payload = agy_stream_input(context.prompt)

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=context.timeout or self._timeout,
            )
        except TimeoutError:
            return self._failure(f"Timeout after {context.timeout or self._timeout}s", start)
        except Exception as exc:
            return self._failure(str(exc), start)
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

        return self._parse_stream_output(
            stdout, process.returncode, stderr, time.time() - start, model
        )

    async def execute_agent_streaming(
        self, context: ExecutionContext, on_delta: Callable[[str], None]
    ) -> AgentResult:
        """Execute via stream-json and invoke on_delta when the result arrives."""
        result = await self.execute_agent(context)
        if result.success and result.output:
            on_delta(result.output)
        return result
