"""OpenCode CLI invokers (agent launch + single-shot analysis).

Runs the installed ``opencode`` CLI (``opencode run``) under the caller's
account authentication, so background agents can use account-backed models —
including free tiers — with no API key configuration.
"""

import subprocess
import time
from collections.abc import Mapping
from typing import Any, Callable

from nexus.adapters.ai.opencode_provider import DEFAULT_MODEL, extract_run_text
from nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers import (
    _launch_process_with_log,
    _prepare_log_path,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.subprocess_utils import (
    run_cli_prompt,
    wrap_timeout_error,
)


def _resolve_model(explicit: str | None) -> str:
    return (explicit or "").strip() or DEFAULT_MODEL


def invoke_opencode_agent_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    opencode_provider: Any,
    opencode_cli_path: str,
    opencode_model: str,
    get_tasks_logs_dir: Callable[[str, str | None], str],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    logger: Any,
    agent_prompt: str,
    workspace_dir: str,
    agents_dir: str,
    issue_num: str | None = None,
    log_subdir: str | None = None,
    env: dict[str, str] | None = None,
    execution_mode: str | None = None,
    execution_mode_config: Mapping[str, Any] | None = None,
) -> int | None:
    if not check_tool_available(opencode_provider):
        raise tool_unavailable_error("OpenCode CLI not available")

    model = _resolve_model(opencode_model)
    cmd = [opencode_cli_path, "run", "--model", model, "--format", "json", agent_prompt]

    log_path = _prepare_log_path(
        prefix="opencode",
        workspace_dir=workspace_dir,
        issue_num=issue_num,
        log_subdir=log_subdir,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )

    logger.info("🤖 Launching OpenCode CLI agent (model: %s)", model)
    logger.info("   Workspace: %s", workspace_dir)
    logger.info("   Log: %s", log_path)

    try:
        process = _launch_process_with_log(
            cmd=cmd,
            workspace_dir=workspace_dir,
            env=env,
            log_path=log_path,
            logger=logger,
            launched_message="🚀 OpenCode launched (PID: %s)",
            output_label="opencode",
        )

        # Detect near-immediate startup failure so orchestrator can fallback.
        # Exit 0 is fine (run-to-completion CLIs finish fast on short prompts).
        exit_code = None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.3)

        if exit_code is not None and exit_code != 0:
            raise tool_unavailable_error(f"OpenCode exited immediately (exit={exit_code})")
        return process.pid
    except Exception as exc:
        logger.error("❌ OpenCode launch failed: %s", exc)
        raise


def run_opencode_analysis_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    opencode_provider: Any,
    opencode_cli_path: str,
    opencode_model: str,
    build_analysis_prompt: Callable[..., str],
    parse_analysis_result: Callable[[str, str], dict[str, Any]],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    text: str,
    task: str,
    timeout: int,
    kwargs: dict[str, Any],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    if not check_tool_available(opencode_provider):
        raise tool_unavailable_error("OpenCode CLI not available")

    prompt = build_analysis_prompt(text, task, **kwargs)
    try:
        cmd = [opencode_cli_path, "run", "--model", _resolve_model(opencode_model),
               "--format", "json", prompt]
        result = run_cli_prompt(cmd, timeout=timeout, env=env, cwd=cwd)
        if result.returncode != 0:
            stderr = result.stderr or ""
            raise Exception(f"OpenCode error: {stderr}")

        return parse_analysis_result(extract_run_text(result.stdout or ""), task)
    except subprocess.TimeoutExpired as exc:
        raise wrap_timeout_error(exc, provider_name="OpenCode", timeout=timeout) from exc
