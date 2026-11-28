"""Pinned original-file lifetime and isolation for local CLI attachment input."""

import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_artifacts import PNG, save
from tests.test_studio_team import team as team
from tests.test_studio_workflow_v2 import wait


@pytest.mark.parametrize("provider", ["codex", "claude-code", "agy", "opencode", "copilot"])
def test_original_files_are_pinned_private_linked_and_removed(tmp_path, provider):
    app = StudioService(tmp_path)
    try:
        first = save(app, b"Original document contents", "evidence (1).txt")
        picture = save(app, PNG, "image.png")
        binary = save(app, b"\x00\xff\x01", "data.bin")
        payload = app.catalog.expand(
            {
                "task": "Review these files",
                "artifact_ids": [first["id"], picture["id"], binary["id"]],
            }
        )
        assert "Original document contents" not in payload["reference_context"]
        save(app, b"Edited later", "evidence (1).txt", id=first["id"], revision=1)
        with app.artifacts.execution_context(payload, provider, "Review") as (prompt, metadata):
            files = [Path(item["path"]) for item in metadata["attachment_files"]]
            assert [file.read_bytes() for file in files] == [
                b"Original document contents",
                PNG,
                b"\x00\xff\x01",
            ]
            assert all(file.as_uri() in prompt for file in files)
            assert all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in files)
            assert stat.S_IMODE(files[0].parent.stat().st_mode) == 0o700
            assert "Original document contents" not in prompt and "untrusted" in prompt
            assert metadata["attachment_files"][0]["revision"] == 1
        assert not any(file.exists() for file in files)
    finally:
        app.close()


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_original_files_live_through_streaming_then_cleanup(tmp_path, terminal):
    import threading

    entered, captured = threading.Event(), []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent_streaming(self, context, on_delta):
            path = Path(context.metadata["attachment_files"][0]["path"])
            captured.append(path)
            assert path.read_bytes() == PNG
            assert path.as_uri() in context.prompt
            entered.set()
            on_delta("Reading selected file")
            await asyncio.sleep(0.01)
            assert path.exists()
            if terminal == "cancelled":
                await asyncio.sleep(3600)
            if terminal == "failed":
                raise ValueError("Synthetic provider error")
            return AgentResult(success=True, output="Reviewed original file", provider_used="codex")

    app = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        picture = save(app, PNG, "image.png")
        run = app.start_run(
            {"task": "Review this image", "provider": "codex", "artifact_ids": [picture["id"]]}
        )
        assert entered.wait(5)
        if terminal == "cancelled":
            app.cancel_run(run["id"])
        wait(app, run, terminal)
        assert captured and not captured[0].exists()
    finally:
        app.close()


def test_new_api_text_context_and_unsupported_binary_are_explicit(tmp_path):
    app = StudioService(tmp_path)
    try:
        text = save(app, b"API source text")
        payload = app.catalog.expand({"task": "Review", "artifact_ids": [text["id"]]})
        with app.artifacts.execution_context(payload, "openai", "Review") as (prompt, metadata):
            assert "API source text" in prompt and "file://" not in prompt and metadata == {}
        binary = save(app, b"\x00\xff", "opaque.bin")
        payload = app.catalog.expand({"task": "Review", "artifact_ids": [binary["id"]]})
        with pytest.raises(ValueError, match="cannot read this file format"):
            with app.artifacts.execution_context(payload, "claude", "Review"):
                pytest.fail("Unsupported binary reached API context")
    finally:
        app.close()


def test_file_names_cannot_escape_and_parallel_invocations_have_distinct_directories(tmp_path):
    app = StudioService(tmp_path)
    try:
        first = save(app, b"one", ".. tricky [name](x).txt")
        second = save(app, b"two", ".. tricky [name](x).txt", id="other-file")
        payload = app.catalog.expand(
            {"task": "Review", "artifact_ids": [first["id"], second["id"]]}
        )
        with app.artifacts.execution_context(payload, "codex", "Review") as (_, one):
            with app.artifacts.execution_context(payload, "codex", "Review") as (_, two):
                assert one["attachment_directory"] != two["attachment_directory"]
                files = [Path(item["path"]) for item in one["attachment_files"]]
                assert files[0] != files[1]
                assert all(file.parent == Path(one["attachment_directory"]) for file in files)
    finally:
        app.close()


def test_team_files_are_scoped_and_only_selected_originals_are_exposed(tmp_path, team):
    app = team[0]
    try:
        photo = save(app, PNG, "selected.png", requester_nexus_id="alice")
        payload = app.catalog.expand(
            {
                "task": "Review",
                "requester_nexus_id": "alice",
                "_role": "owner",
                "artifact_ids": [photo["id"]],
            }
        )
        with app.artifacts.execution_context(payload, "claude-code", "Review") as (
            prompt,
            metadata,
        ):
            assert metadata["attachment_files"][0]["path"].startswith("/workspace/attachments/")
            assert metadata["attachment_directory"] not in prompt
            assert len(list(Path(metadata["attachment_directory"]).iterdir())) == 1
        payload["requester_nexus_id"] = "bob"
        payload["_role"] = "operator"
        with pytest.raises(PermissionError):
            with app.artifacts.execution_context(payload, "claude-code", "Review"):
                pytest.fail("Other user's original exposed")
    finally:
        app.close()


@pytest.mark.parametrize("provider", ["codex", "claude-code"])
def test_shared_worker_mounts_only_attachment_directory_with_scoped_read(
    tmp_path, monkeypatch, provider
):
    from unittest.mock import AsyncMock, MagicMock

    from nexus.adapters.ai.base import ExecutionContext
    from nexus.core.studio_team import ContainerCLIProvider

    directory = tmp_path / "attachments"
    directory.mkdir()
    (directory / "selected.bin").write_bytes(b"selected original")
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(
        return_value=(b'{"result":"ok"}' if provider == "claude-code" else b"ok", b"")
    )
    cleanup = MagicMock(returncode=0)
    cleanup.wait = AsyncMock(return_value=0)
    spawn = AsyncMock(side_effect=[process, cleanup])
    monkeypatch.setattr("nexus.core.studio_team.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("nexus.core.studio_team.asyncio.create_subprocess_exec", spawn)
    context = ExecutionContext(
        "test",
        "Read selected files",
        tmp_path,
        metadata={"worker_tools": "web", "attachment_directory": str(directory)},
    )
    result = asyncio.run(
        ContainerCLIProvider(provider, {}, "trusted-worker@sha256:" + "a" * 64).execute_agent(
            context
        )
    )
    assert result.success
    args = list(spawn.call_args_list[0].args)
    assert f"type=bind,src={directory},dst=/workspace/attachments,readonly" in args
    assert "--read-only" in args and "Bash" not in ",".join(args)
    if provider == "claude-code":
        assert args[args.index("--tools") + 1] == "WebSearch,WebFetch,Read"
        assert args[args.index("--allowedTools") + 1] == "WebSearch,WebFetch,Read(./attachments/**)"


def test_quick_build_receives_original_file_and_removes_it_after_generation(tmp_path):
    from tests.test_quick_build import proposal

    paths = []

    class Builder:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            path = Path(context.metadata["attachment_files"][0]["path"])
            paths.append(path)
            assert path.read_bytes() == PNG and path.as_uri() in context.prompt
            return AgentResult(success=True, output=json.dumps(proposal()), provider_used="codex")

    app = StudioService(tmp_path, builder_factory=lambda _: Builder())
    try:
        picture = save(app, PNG, "image.png")
        run = app.start_run(
            {
                "kind": "build",
                "task": "Design a team from this image",
                "provider": "codex",
                "artifact_ids": [picture["id"]],
            }
        )
        wait(app, run)
        assert paths and not paths[0].exists()
    finally:
        app.close()


def test_actual_cli_subprocess_can_read_original_from_prompt_link(tmp_path):
    from nexus.adapters.ai.codex_provider import CodexCLIProvider

    shim = tmp_path / "local-fixture-cli"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\nfrom pathlib import Path\n"
        "if '--version' in sys.argv:\n print('local attachment fixture');sys.exit(0)\n"
        "prompt=sys.stdin.read()\n"
        "paths=[json.loads(line.strip().split('Local path: ',1)[1]) for line in prompt.splitlines() if line.strip().startswith('Local path: ')]\n"
        "assert len(paths)==1 and Path(paths[0]).read_bytes()==b'x\\x00\\xff'\n"
        "print('Read all three original bytes through the CLI file path')\n"
    )
    shim.chmod(0o700)
    app = StudioService(
        tmp_path / "workspace", provider_factory=lambda _: CodexCLIProvider(binary=str(shim))
    )
    try:
        file = save(app, b"x\x00\xff", "source.bin")
        result = wait(
            app,
            app.start_run(
                {"provider": "codex", "task": "Read source", "artifact_ids": [file["id"]]}
            ),
        )
        assert "Read all three original bytes" in result["output"]
    finally:
        app.close()


def test_local_claude_grants_only_selected_temporary_directory(tmp_path):
    from nexus.adapters.ai.base import ExecutionContext
    from nexus.adapters.ai.claude_code_provider import ClaudeCodeProvider

    selected = tmp_path / "selected files"
    context = ExecutionContext(
        "test", "Read", tmp_path, metadata={"attachment_directory": str(selected)}
    )
    args, _ = ClaudeCodeProvider()._cmd(context, "json")
    assert args[args.index("--add-dir") + 1] == str(selected)
    assert args.count("--add-dir") == 1
