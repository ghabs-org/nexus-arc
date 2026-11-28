"""ClaudeCodeProvider: `claude -p` JSON parsing, stdin prompt and failure paths."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from nexus.adapters.ai.base import ExecutionContext
from nexus.adapters.ai.claude_code_provider import ClaudeCodeProvider


def _run(stdout: bytes, returncode: int = 0, **kwargs):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, b"boom"))
    proc.returncode = returncode
    with patch(
        "nexus.adapters.ai.claude_code_provider.asyncio.create_subprocess_exec", return_value=proc
    ) as spawn:
        ctx = ExecutionContext(agent_name="chat", prompt="hello", workspace=Path("/tmp"), **kwargs)
        result = asyncio.run(ClaudeCodeProvider(model="sonnet", extra_args=["--x"]).execute_agent(ctx))
    return result, spawn, proc


def test_success_parses_result_and_usage():
    out = json.dumps({"result": "hi there", "is_error": False, "total_cost_usd": 0.01,
                      "usage": {"input_tokens": 3, "output_tokens": 5}}).encode()
    result, spawn, proc = _run(out, model_override="opus")

    assert result.success is True and result.output == "hi there"
    assert result.provider_used == "claude-code"
    assert result.metadata["usage"]["output_tokens"] == 5
    args = spawn.call_args.args
    assert args[:2] == ("claude", "-p") and args[-3:] == ("--model", "opus", "--x")
    proc.communicate.assert_awaited_once_with(b"hello")


def test_cli_error_and_garbage_output_fail():
    err = json.dumps({"result": "Not logged in", "is_error": True}).encode()
    result, _, _ = _run(err, returncode=1)
    assert result.success is False and result.error == "Not logged in"

    result, _, _ = _run(b"not json", returncode=1)
    assert result.success is False and result.error == "boom"


def _stream(lines, returncode: int | None = 0):
    """returncode None = the CLI is still running when the stream is cut short (it gets killed)."""
    proc = MagicMock()
    proc.stdout.__aiter__.return_value = [
        line if isinstance(line, bytes) else json.dumps(line).encode() + b"\n" for line in lines
    ]
    proc.stdin.drain = AsyncMock()
    proc.stderr.read = AsyncMock(return_value=b"boom")
    proc.wait = AsyncMock(return_value=returncode if returncode is not None else -9)
    proc.returncode = returncode
    deltas: list[str] = []
    with patch(
        "nexus.adapters.ai.claude_code_provider.asyncio.create_subprocess_exec", return_value=proc
    ) as spawn:
        ctx = ExecutionContext(agent_name="chat", prompt="hello", workspace=Path("/tmp"))
        result = asyncio.run(ClaudeCodeProvider(extra_args=["--x"]).execute_agent_streaming(ctx, deltas.append))
    return result, deltas, spawn, proc


def _ev(kind, parent=None, **fields):
    return {"type": "stream_event", "parent_tool_use_id": parent, "event": {"type": kind, **fields}}


def _delta(text, parent=None, kind="text_delta"):
    key = "text" if kind == "text_delta" else "partial_json"
    return _ev("content_block_delta", parent, delta={"type": kind, key: text})


def _stop(reason, output_tokens=0):
    return [_ev("message_delta", delta={"stop_reason": reason}, usage={"output_tokens": output_tokens}),
            _ev("message_stop")]


def test_streaming_finishes_at_end_turn_without_waiting_for_result():
    result, deltas, spawn, proc = _stream([
        {"type": "system", "subtype": "init"},
        _ev("message_start"),
        _delta("Hel"),
        b"not json\n",
        _delta("{}", kind="input_json_delta"),
        _delta("subagent text", parent="toolu_1"),
        _ev("message_stop", parent="toolu_1"),
        _delta("lo"),
        *_stop("end_turn", output_tokens=2),
        {"type": "system", "subtype": "post_turn_summary"},
        {"type": "result", "is_error": False, "result": "never read"},
    ], returncode=None)

    assert deltas == ["Hel", "lo"]
    assert result.success is True and result.output == "Hello"
    assert result.metadata["usage"]["output_tokens"] == 2
    proc.kill.assert_called_once()  # the post-turn summary is not waited for
    args = spawn.call_args.args
    assert args[:4] == ("claude", "-p", "--output-format", "stream-json")
    assert "--include-partial-messages" in args and args[-1] == "--x"
    proc.stdin.write.assert_called_once_with(b"hello")
    proc.stdin.close.assert_called_once()


def test_streaming_output_is_the_last_message_of_a_tool_using_turn():
    result, deltas, _, _ = _stream([
        _ev("message_start"), _delta("Let me check. "), *_stop("tool_use"),
        _ev("message_start"), _delta("Done."), *_stop("end_turn"),
    ], returncode=None)
    assert deltas == ["Let me check. ", "Done."] and result.output == "Done."


def test_streaming_other_stop_reasons_wait_for_result():
    result, _, _, proc = _stream([
        _ev("message_start"), _delta("Cut"), *_stop("max_tokens"),
        {"type": "result", "is_error": False, "result": "Cut off"},
    ])
    assert result.success is True and result.output == "Cut off"
    proc.kill.assert_not_called()


def test_streaming_failures():
    result, _, _, _ = _stream([{"type": "result", "is_error": True, "result": "Not logged in"}], returncode=1)
    assert result.success is False and result.error == "Not logged in"

    result, _, _, _ = _stream([_delta("partial")], returncode=1)  # died before the result event
    assert result.success is False and result.error == "boom"
