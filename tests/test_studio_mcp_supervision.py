"""Portable selection, redaction and scoped-identity guards for MCP cleanup."""

import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core import studio_mcp_supervisor as supervisor
from nexus.tools import mcp_supervision


def test_speech_supervisor_refuses_hidden_child_view_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "_configure", lambda: tmp_path / "unavailable")
    monkeypatch.setattr(
        supervisor.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Worker must not start"),
    )
    with pytest.raises(OSError):
        supervisor.supervise_speech(["never-launch"])


@pytest.mark.parametrize("frame", [b'{"input": 123}\n', b'{"input":"not base64!"}\n'])
def test_speech_supervisor_rejects_invalid_input_before_launch(tmp_path, monkeypatch, frame):
    children = tmp_path / "children"
    children.touch()
    monkeypatch.setattr(supervisor, "_configure", lambda: children)
    monkeypatch.setattr(supervisor.signal, "signal", lambda *_: None)
    monkeypatch.setattr(supervisor.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(frame)))
    monkeypatch.setattr(
        supervisor.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Worker must not start"),
    )
    with pytest.raises(ValueError):
        supervisor.supervise_speech(["never-launch"])


def test_source_supervision_keeps_literal_arguments_and_no_shell(monkeypatch):
    monkeypatch.setattr(mcp_supervision, "available", lambda: True)
    args = ["literal; command", "$(not-executed)"]
    command, values = mcp_supervision.supervised_command("selected-server", args)
    assert command == sys.executable and values[0] == "-I"
    assert Path(values[1]).name == "studio_mcp_supervisor.py"
    assert values[2:] == ["--", "selected-server", *args]
    assert args == ["literal; command", "$(not-executed)"]


def test_frozen_supervisor_uses_trusted_dispatch(monkeypatch):
    monkeypatch.setattr(mcp_supervision, "available", lambda: True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command, args = mcp_supervision.supervised_command("selected-server", [])
    assert command == sys.executable
    assert args == ["--nexus-worker", "nexus.core.studio_mcp_supervisor", "--", "selected-server"]


def test_unavailable_linux_retains_sdk_policy_with_explicit_warning(monkeypatch, caplog):
    monkeypatch.setattr(mcp_supervision, "available", lambda: False)
    monkeypatch.setattr(mcp_supervision.sys, "platform", "linux")
    assert mcp_supervision.supervised_command("selected-server", ["arg"]) == (
        "selected-server",
        ["arg"],
    )
    assert "SDK process-group cleanup only" in caplog.text


def test_server_environment_stays_in_sdk_parameters_not_command_line(monkeypatch):
    from nexus.tools.mcp import _stdio_parameters

    monkeypatch.setattr(mcp_supervision, "available", lambda: True)
    result = _stdio_parameters(SimpleNamespace, "server", ["argument"], {"TOKEN": "PRIVATE_SECRET"})
    assert result.env == {"TOKEN": "PRIVATE_SECRET"}
    assert "PRIVATE_SECRET" not in repr((result.command, result.args))


@pytest.mark.parametrize("owned", [False, True])
def test_cleanup_signals_only_checked_children_using_stable_pidfds(tmp_path, monkeypatch, owned):
    own = os.getpid()
    children = tmp_path / "children"
    children.write_text("111")
    real_open = Path.open
    calls, closed, attempts = [], [], []

    def wait(*args):
        if attempts:
            raise ChildProcessError
        attempts.append(True)
        return 0, 0

    def open_file(path, *args, **kwargs):
        if str(path) == "/proc/111/stat":
            return io.BytesIO(f"111 (fixture) S {own if owned else own + 1000} 0 0".encode())
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(supervisor.os, "waitpid", wait)
    monkeypatch.setattr(supervisor.os, "pidfd_open", lambda pid: 71, raising=False)
    monkeypatch.setattr(
        supervisor.signal,
        "pidfd_send_signal",
        lambda fd, sig: calls.append((fd, sig)),
        raising=False,
    )
    original_close = os.close
    monkeypatch.setattr(
        supervisor.os, "close", lambda fd: closed.append(fd) if fd == 71 else original_close(fd)
    )
    monkeypatch.setattr(Path, "open", open_file)
    supervisor._cleanup(children)
    assert calls == ([(71, supervisor.signal.SIGKILL)] if owned else [])
    assert closed == [71]


def test_unreadable_child_view_is_not_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor.os, "waitpid", lambda *a: (0, 0))
    assert supervisor._cleanup(tmp_path / "missing") is False


def test_launch_diagnostics_never_disclose_server_configuration(monkeypatch, capsys):
    def fail(command):
        raise OSError("PRIVATE_SERVER_ARGUMENTS")

    monkeypatch.setattr(supervisor, "supervise", fail)
    monkeypatch.setattr(sys, "argv", ["supervisor", "--", "server", "PRIVATE_SERVER_ARGUMENTS"])
    with pytest.raises(SystemExit) as result:
        supervisor.main()
    output = capsys.readouterr()
    assert result.value.code == 125 and output.out == ""
    assert "PRIVATE_SERVER_ARGUMENTS" not in output.err


def test_frozen_supervisor_file_dispatch_does_not_import_studio(tmp_path, monkeypatch, capsys):
    from nexus.core.studio_desktop_bundle import FROZEN_ENTRYPOINT

    worker = tmp_path / "nexus/core/studio_mcp_supervisor.py"
    worker.parent.mkdir(parents=True)
    worker.write_text("print('isolated-supervisor-dispatch')")
    monkeypatch.setattr(
        sys, "argv", ["sidecar", "--nexus-worker", "nexus.core.studio_mcp_supervisor"]
    )
    exec(FROZEN_ENTRYPOINT, {"__file__": str(tmp_path / "entry.py")})
    assert capsys.readouterr().out.strip() == "isolated-supervisor-dispatch"
