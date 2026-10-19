"""Claude Code CLI AI provider (`claude -p`): runs on the local Claude Code login, no API key."""

import asyncio
import json
import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.core.models import AgentResult, RateLimitStatus

logger = logging.getLogger(__name__)

_CLAUDE_CODE_PREFERRED_TASKS = {"reasoning", "analysis", "code_generation", "code_review"}


class ClaudeCodeProvider(AIProvider):
    """AI provider that delegates to the Claude Code CLI in print mode."""

    def __init__(
        self,
        timeout: int = 600,
        binary: str = "claude",
        model: str | None = None,
        extra_args: list[str] | None = None,
    ):
        self._timeout = timeout
        self._binary = binary
        self._model = model
        self._extra_args = extra_args or []
        self._availability_cache: dict = {}
        self._availability_ttl: int = 300  # seconds

    @property
    def name(self) -> str:
        return "claude-code"

    async def check_availability(self) -> bool:
        """Return True if the claude CLI binary is installed and runnable."""
        now = time.time()
        cached = self._availability_cache.get("claude-code")
        if cached and now - cached["at"] < self._availability_ttl:
            return cached["available"]

        available = bool(shutil.which(self._binary))
        if available:
            try:
                result = subprocess.run([self._binary, "--version"], capture_output=True, timeout=10)
                available = result.returncode == 0
            except Exception:
                available = False

        self._availability_cache["claude-code"] = {"available": available, "at": now}
        return available

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Subscription limits are not exposed programmatically; report unlimited."""
        return RateLimitStatus(provider=self.name, is_limited=False)

    def get_preference_score(self, task_type: str) -> float:
        """Return 0.9 for reasoning/coding tasks, 0.75 otherwise."""
        return 0.9 if task_type in _CLAUDE_CODE_PREFERRED_TASKS else 0.75

    def _cmd(self, context: ExecutionContext, output_format: str) -> tuple[list[str], str | None]:
        cmd = [self._binary, "-p", "--output-format", output_format, "--no-session-persistence"]
        if output_format == "stream-json":
            cmd.extend(["--verbose", "--include-partial-messages"])
        model = context.model_override or self._model
        if model:
            cmd.extend(["--model", model])
        cmd.extend(self._extra_args)
        return cmd, model

    def _failure(self, error: str, start: float) -> AgentResult:
        return AgentResult(
            success=False, output="", error=error,
            execution_time=time.time() - start, provider_used=self.name,
        )

    def _result(self, res: dict, returncode, stderr: bytes, elapsed: float, model) -> AgentResult:
        """AgentResult from the CLI's final JSON (the whole `json` output or the stream's `result` event)."""
        output = str(res.get("result") or "")
        if returncode != 0 or res.get("is_error") or not res:
            return AgentResult(
                success=False,
                output=output,
                error=output or stderr.decode(errors="replace").strip() or f"claude exited {returncode}",
                execution_time=elapsed,
                provider_used=self.name,
            )
        usage = res.get("usage") or {}
        return AgentResult(
            success=True,
            output=output,
            execution_time=elapsed,
            provider_used=self.name,
            metadata={
                "model": model,
                "cost_usd": res.get("total_cost_usd"),
                "usage": {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                },
            },
        )

    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        """Run the prompt through `claude -p --output-format json` (prompt on stdin)."""
        start = time.time()
        workspace = Path(context.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        cmd, model = self._cmd(context, "json")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(context.prompt.encode()),
                timeout=context.timeout or self._timeout,
            )
        except TimeoutError:
            return self._failure(f"Timeout after {context.timeout or self._timeout}s", start)
        except Exception as exc:
            return self._failure(str(exc), start)

        try:
            res = json.loads(stdout or b"{}")
        except ValueError:
            res = {}
        return self._result(res, process.returncode, stderr, time.time() - start, model)

    async def execute_agent_streaming(
        self, context: ExecutionContext, on_delta: Callable[[str], None]
    ) -> AgentResult:
        """Like execute_agent, but via `--output-format stream-json`: calls on_delta(text) for each
        text delta as the model writes. Returns as soon as the last top-level message stops with
        `end_turn` (output = that message's text, the CLI is killed), instead of waiting seconds for
        `result` behind the CLI's post-turn summary call. Intermediate text from tool-using turns is
        streamed too but isn't in the output. Other stop reasons fall back to the `result` event.
        Assumes nothing resumes the turn after `end_turn` (no Stop hooks: run with --setting-sources "")."""
        start = time.time()
        workspace = Path(context.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        cmd, model = self._cmd(context, "stream-json")
        timeout = context.timeout or self._timeout

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
                limit=1 << 24,  # one event per line; the final assistant/result lines repeat the whole reply
            )
        except Exception as exc:
            return self._failure(str(exc), start)

        async def consume() -> tuple[dict, int | None, bytes]:
            stderr = asyncio.create_task(process.stderr.read())  # drain so a chatty stderr can't block
            process.stdin.write(context.prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            res: dict = {}
            text: list[str] = []
            usage: dict = {}
            stop_reason = None
            async for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "result":
                    res = event
                if event.get("type") != "stream_event" or event.get("parent_tool_use_id"):
                    continue
                inner = event.get("event") or {}
                delta = inner.get("delta") or {}
                if inner.get("type") == "message_start":
                    text, stop_reason = [], None
                elif delta.get("type") == "text_delta" and delta.get("text"):
                    text.append(delta["text"])
                    on_delta(delta["text"])
                elif inner.get("type") == "message_delta":
                    stop_reason, usage = delta.get("stop_reason"), inner.get("usage") or {}
                elif inner.get("type") == "message_stop" and stop_reason == "end_turn":
                    stderr.cancel()
                    return {"result": "".join(text), "usage": usage}, 0, b""
            return res, await process.wait(), await stderr

        try:
            res, returncode, stderr = await asyncio.wait_for(consume(), timeout=timeout)
        except TimeoutError:
            return self._failure(f"Timeout after {timeout}s", start)
        except Exception as exc:
            return self._failure(str(exc), start)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return self._result(res, returncode, stderr, time.time() - start, model)
