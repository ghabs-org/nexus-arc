"""Optional real-library/PTY acceptance. Run explicitly in the studio-terminal extra.

PYTHONPATH=. python -m pytest tests/studio_terminal_ui_acceptance.py -q -o addopts=''
Requires pytest and pexpect in addition to nexus-arc[studio-terminal]. No account
or existing Studio instance is used. Base-install tests never skip these checks.
"""

import io
import os
import re
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pexpect
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_terminal_sessions import TerminalSessions
from nexus.core.studio_terminal_ui import make_completer, render_markdown
from nexus.studio import make_studio_handler


class TTYBuffer(io.StringIO):
    def isatty(self):
        return True


def test_real_rich_renders_headings_code_lists_and_tables(monkeypatch):
    output = TTYBuffer()
    monkeypatch.setattr("sys.stdout", output)
    render_markdown(
        "# Report\n\n- Evidence\n\n```python\nprint(42)\n```\n\n"
        "| Source | Count |\n| --- | --- |\n| Local | 2 |\n\n[bold]literal[/bold]",
        label="assistant:",
    )
    rendered = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output.getvalue())
    assert "Report" in rendered and "Evidence" in rendered and "print(42)" in rendered
    assert "Source" in rendered and "Local" in rendered and "Count" in rendered
    assert "# Report" not in rendered and "```python" not in rendered
    assert rendered.startswith("assistant:\n")
    assert "[bold]literal[/bold]" in rendered


def test_real_completer_is_explicit_and_preserves_literal_command_text():
    completer = make_completer(TerminalSessions(None))
    assert list(completer.get_completions(Document("/he"), CompleteEvent())) == []
    results = list(
        completer.get_completions(Document("/he"), CompleteEvent(completion_requested=True))
    )
    assert [(item.text, item.start_position) for item in results] == [("/help", -3)]


def test_actual_pty_completion_markdown_and_multiline_never_duplicate_tasks(tmp_path):
    contexts = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            contexts.append(context)
            return AgentResult(
                success=True, output="# Report\n\n**Reviewed evidence**\n\n- Local source"
            )

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "ui-fixture"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    child = None
    try:
        child = pexpect.spawn(
            sys.executable,
            [
                "-m",
                "nexus.cli",
                "chat",
                "--url",
                f"http://127.0.0.1:{server.server_port}",
                "--timeout",
                "10",
            ],
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "PROMPT_TOOLKIT_NO_CPR": "1",
                "TERM": "xterm-256color",
                "NEXUS_TERMINAL_STATE_DIR": str(tmp_path / "terminal-preferences"),
            },
            encoding="utf-8",
            timeout=20,
            dimensions=(32, 120),
        )
        child.expect_exact("You:")
        child.send("/he\t")
        child.expect_exact("lp")
        child.sendline("")
        child.expect_exact("Other slash messages retain Studio prompt-shortcut behavior.")
        child.expect_exact("You:")
        assert not studio.records("runs")
        child.sendline("Evidence")
        child.expect_exact("Reviewed evidence")
        child.expect_exact("You:")
        assert len(studio.records("runs")) == 1
        child.sendline("/retry")
        child.expect_exact("Reviewed evidence")
        child.expect_exact("You:")
        assert len(studio.records("runs")) == 2
        child.sendline("/rename Review evidence")
        child.expect_exact("Renamed conversation to Review evidence")
        child.expect_exact("You:")
        child.send("/switch Rev\t")
        child.expect_exact("iew evidence")
        child.sendline("")
        child.expect_exact("Selected ")
        child.expect_exact("You:")
        assert len(studio.records("runs")) == 2
        child.sendline("/alias recent /history 1")
        child.expect_exact("Saved alias /recent")
        child.expect_exact("You:")
        child.sendline("/recent")
        child.expect_exact("Reviewed evidence")
        child.expect_exact("You:")
        child.sendline("/alias recent")
        child.expect_exact("Removed alias /recent")
        child.expect_exact("You:")
        child.sendline("/c")
        child.expect_exact("\x1b[2J\x1b[H")
        child.expect_exact("You:")
        assert len(studio.records("runs")) == 2
        child.sendline("/m")
        child.expect_exact("Multiline enabled; Alt+Enter sends.")
        child.expect_exact("You:")
        child.send("First line\nSecond line")
        child.send("\x1b\r")
        child.expect_exact("Reviewed evidence")
        child.expect_exact("You:")
        child.send("/multiline\x1b\r")
        child.expect_exact("Multiline disabled.")
        child.expect_exact("You:")
        child.sendline("/quit")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
        assert len(contexts) == len(studio.records("runs")) == 3
        assert "First line\nSecond line" in contexts[-1].prompt
        assert any(run["task"] == "First line\nSecond line" for run in studio.records("runs"))
        # The explicit escape hatch also works on an actual terminal with both
        # optional libraries installed, rather than only under redirected tests.
        child = pexpect.spawn(
            sys.executable,
            [
                "-m",
                "nexus.cli",
                "chat",
                "--plain",
                "--url",
                f"http://127.0.0.1:{server.server_port}",
                "--timeout",
                "10",
            ],
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "NEXUS_TERMINAL_STATE_DIR": str(tmp_path / "terminal-preferences"),
            },
            encoding="utf-8",
            timeout=20,
        )
        child.expect_exact("You:")
        child.sendline("/multiline")
        child.expect_exact("Command failed:")
        child.expect_exact("You:")
        child.sendline("Plain evidence")
        child.expect_exact("# Report")
        child.expect_exact("**Reviewed evidence**")
        child.expect_exact("You:")
        child.sendline("/quit")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
        assert len(contexts) == len(studio.records("runs")) == 4
    finally:
        if child is not None and child.isalive():
            child.close(force=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.close()
