"""Tests for Studio assistant, server workspace files and their HTTP routes."""

import base64
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


@pytest.fixture
def service_env(tmp_path):
    class DummyProvider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=True, output="Platform help verified: " + context.prompt[:50])

    service = StudioService(tmp_path, provider_factory=lambda name: DummyProvider())
    service.save_definition(
        "agents",
        {"id": "doc-helper", "name": "Doc Helper", "instructions": "Help with documentation"},
    )
    service.save_definition(
        "workflows",
        {
            "id": "doc-flow",
            "name": "Doc Flow",
            "steps": [{"id": "step_1", "agent_type": "doc-helper", "prompt": "Review"}],
        },
    )
    yield service
    service.close()


def test_studio_assistant_local_help_and_metadata(service_env):
    help_data = service_env.assistant.answer()
    assert help_data["mode"] == "local-help"
    assert "agents" in help_data["workspace"]
    assert any(item["id"] == "doc-helper" for item in help_data["resources"]["agents"])
    assert any(item["id"] == "doc-flow" for item in help_data["resources"]["workflows"])

    ranked = service_env.assistant.answer("Where are my agents saved?")
    assert "storage" in [s["id"] for s in ranked["sources"]]

    with pytest.raises(ValueError, match="up to 2000 characters"):
        service_env.assistant.answer("a" * 2001)


def test_studio_assistant_spec_and_inventory(service_env):
    spec = service_env.assistant.spec()
    assert "purpose" in spec
    assert len(spec["tool_ids"]) == 1
    assert spec["tool_ids"][0].endswith(":studio_help")

    inventory = service_env.assistant.inventory()
    assert len(inventory["items"]) == 3
    tool_ids = [item["id"] for item in inventory["items"]]
    assert any(tid.endswith(":studio_help") for tid in tool_ids)
    assert any(tid.endswith(":workspace_list") for tid in tool_ids)
    assert any(tid.endswith(":workspace_read") for tid in tool_ids)


def test_workspace_files_roots_and_validation(service_env, tmp_path):
    # Default roots: files, agents, workflows
    assert set(service_env.workspace_files.roots.keys()) == {"files", "agents", "workflows"}
    assert service_env.workspace_files.roots["files"]["writable"] is True
    assert service_env.workspace_files.roots["agents"]["writable"] is False
    assert service_env.workspace_files.roots["workflows"]["writable"] is False

    # Roots validation: cannot expose workspace root or state
    from nexus.core.studio_workspace_files import StudioWorkspaceFiles

    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    custom = StudioWorkspaceFiles(
        service_env, roots={"extra": {"path": str(extra_dir), "writable": False}}
    )
    assert "extra" in custom.roots

    with pytest.raises(ValueError, match="workspace root or private Studio state"):
        StudioWorkspaceFiles(
            service_env, roots={"root-bad": {"path": str(service_env.root), "writable": False}}
        )

    with pytest.raises(ValueError, match="at most twelve"):
        bad_roots = {f"r{i}": {"path": str(extra_dir)} for i in range(13)}
        StudioWorkspaceFiles(service_env, roots=bad_roots)


def test_workspace_files_upload_listing_preview_and_snapshot(service_env):
    files = service_env.workspace_files

    # 1. Upload file
    content = b"Sample text file content."
    res = files.upload(
        {
            "root": "files",
            "path": "",
            "filename": "sample.txt",
            "content_base64": base64.b64encode(content).decode(),
        }
    )
    assert res["path"] == "sample.txt"
    assert res["size"] == len(content)

    # Re-upload same file should fail (no overwrite)
    with pytest.raises(ValueError):
        files.upload(
            {
                "root": "files",
                "path": "",
                "filename": "sample.txt",
                "content_base64": base64.b64encode(content).decode(),
            }
        )

    # Upload to read-only root should fail
    with pytest.raises(PermissionError):
        files.upload(
            {
                "root": "agents",
                "path": "",
                "filename": "agent.yaml",
                "content_base64": base64.b64encode(content).decode(),
            }
        )

    # 2. Folder creation
    folder_res = files.folder({"root": "files", "path": "", "name": "subfolder"})
    assert folder_res["created"] is True

    # 3. Listing
    listing = files.listing("files", "")
    assert listing["root"] == "files"
    assert listing["writable"] is True
    names = [i["name"] for i in listing["items"]]
    assert "subfolder" in names
    assert "sample.txt" in names

    # 4. Preview
    preview = files.preview("files", "sample.txt")
    assert preview["text"] == "Sample text file content."
    assert preview["revision"] == res["revision"]
    assert preview["edit_view"] is None

    # Preview of agent definition has edit_view
    agent_preview = files.preview("agents", "doc-helper.yaml")
    assert agent_preview["edit_view"] == "#agents"
    assert "Doc Helper" in agent_preview["text"]

    # 5. Snapshot
    snapshot = files.snapshot(
        {"root": "files", "path": "sample.txt", "revision": preview["revision"]}
    )
    assert snapshot["artifact_id"].startswith("workspace-")
    assert snapshot["source_revision"] == preview["revision"]
    assert snapshot["download_url"].startswith("/api/catalog/artifacts/")

    # Stale revision snapshot rejected
    with pytest.raises(ValueError, match="File changed"):
        files.snapshot(
            {"root": "files", "path": "sample.txt", "revision": "wrong-revision-hash"}
        )


def test_workspace_files_path_filtering_and_security(service_env):
    files = service_env.workspace_files
    files_dir = service_env.root / "files"
    files_dir.mkdir(exist_ok=True)

    # Hidden files and credential names are hidden from listing
    (files_dir / ".hidden").write_text("hidden")
    (files_dir / "id_rsa").write_text("ssh-key")
    (files_dir / "secret.key").write_text("secret")
    (files_dir / "studio.env").write_text("env")

    listing = files.listing("files", "")
    names = [i["name"] for i in listing["items"]]
    assert ".hidden" not in names
    assert "id_rsa" not in names
    assert "secret.key" not in names
    assert "studio.env" not in names

    # Traversal in read rejected
    with pytest.raises(ValueError, match="traversal"):
        files.read("files", "../secret.txt")
    with pytest.raises(ValueError, match="traversal"):
        files.read("files", "id_rsa")


def test_studio_assistant_run_execution(service_env):
    # Studio assistant run
    run = service_env.start_run(
        {"kind": "chat", "studio_assistant": True, "task": "Where are files?"}
    )
    assert run["status"] in {"queued", "running", "completed"}

    # Studio assistant with custom agent_id rejected
    with pytest.raises(ValueError, match="without a custom agent"):
        service_env.start_run(
            {"kind": "chat", "studio_assistant": True, "agent_id": "doc-helper", "task": "Test"}
        )

    # Studio assistant with non-chat kind rejected
    with pytest.raises(ValueError, match="without a custom agent"):
        service_env.start_run(
            {"kind": "workflow", "studio_assistant": True, "task": "Test"}
        )

    # Non-boolean studio_assistant rejected
    with pytest.raises(ValueError, match="must be true or false"):
        service_env.start_run(
            {"kind": "chat", "studio_assistant": "yes", "task": "Test"}
        )


def test_studio_workspace_access_http_endpoints(service_env):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(service_env, "test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(path, method="GET", body=None, headers=None):
        req = urllib.request.Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
            method=method,
        )
        try:
            return urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            return exc

    try:
        # GET /api/studio/assistant
        with request("/api/studio/assistant") as res:
            assert res.status == 200
            data = json.load(res)
            assert data["mode"] == "local-help"

        # POST /api/studio/assistant/answer
        with request(
            "/api/studio/assistant/answer",
            method="POST",
            body={"question": "storage"},
            headers={"X-Nexus-Studio-Token": "test-token"},
        ) as res:
            assert res.status == 200
            data = json.load(res)
            assert "storage" in [s["id"] for s in data["sources"]]

        # POST /api/studio/files/upload
        file_bytes = b"Uploaded via HTTP endpoint."
        with request(
            "/api/studio/files/upload",
            method="POST",
            body={
                "root": "files",
                "path": "",
                "filename": "http_file.txt",
                "content_base64": base64.b64encode(file_bytes).decode(),
            },
            headers={"X-Nexus-Studio-Token": "test-token"},
        ) as res:
            assert res.status == 201
            data = json.load(res)
            assert data["path"] == "http_file.txt"
            file_rev = data["revision"]

        # GET /api/studio/files
        with request("/api/studio/files?root=files") as res:
            assert res.status == 200
            data = json.load(res)
            assert any(item["name"] == "http_file.txt" for item in data["items"])

        # GET /api/studio/files/preview
        with request("/api/studio/files/preview?root=files&path=http_file.txt") as res:
            assert res.status == 200
            data = json.load(res)
            assert data["text"] == "Uploaded via HTTP endpoint."
            assert data["revision"] == file_rev

        # POST /api/studio/files/snapshot
        with request(
            "/api/studio/files/snapshot",
            method="POST",
            body={"root": "files", "path": "http_file.txt", "revision": file_rev},
            headers={"X-Nexus-Studio-Token": "test-token"},
        ) as res:
            assert res.status == 201
            data = json.load(res)
            assert data["artifact_id"].startswith("workspace-")
            assert data["source_revision"] == file_rev
    finally:
        server.shutdown()
        server.server_close()
