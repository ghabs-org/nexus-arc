"""Tests for Studio assistant, server workspace files and their HTTP routes."""

import base64
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler
from tests.test_studio_team import team as team
from tests.test_studio_team import wait


@pytest.fixture
def service_env(tmp_path):
    class DummyProvider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(
                success=True, output="Platform help verified: " + context.prompt[:50]
            )

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
    assert [tid.rsplit(":", 1)[1] for tid in spec["tool_ids"]] == [
        "studio_help",
        "workspace_list",
        "workspace_read",
        "workspace_write",
    ]

    inventory = service_env.assistant.inventory()
    assert len(inventory["items"]) == 4
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
        files.snapshot({"root": "files", "path": "sample.txt", "revision": "wrong-revision-hash"})


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
    assert run.get("studio_assistant") is True

    # Studio assistant with custom agent_id rejected
    with pytest.raises(ValueError, match="without a custom agent"):
        service_env.start_run(
            {"kind": "chat", "studio_assistant": True, "agent_id": "doc-helper", "task": "Test"}
        )

    # Studio assistant with non-chat kind rejected
    with pytest.raises(ValueError, match="without a custom agent"):
        service_env.start_run({"kind": "workflow", "studio_assistant": True, "task": "Test"})

    # Non-boolean studio_assistant rejected
    with pytest.raises(ValueError, match="must be true or false"):
        service_env.start_run({"kind": "chat", "studio_assistant": "yes", "task": "Test"})


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


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../secret",
        "sub/../../secret",
        "sub/.env",
        "a\\b",
        "a//b",
        "a/",
        "credentials.json",
        "secret.pem",
        "nul\x00.txt",
    ],
)
def test_file_routes_reject_unsafe_path_inputs(service_env, path):
    with pytest.raises(ValueError):
        service_env.workspace_files.read("files", path)


def test_symlinks_hardlinks_and_fifo_are_not_exposed(service_env, tmp_path):
    root = service_env.root / "files"
    root.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("PRIVATE")
    (root / "link.txt").symlink_to(secret)
    os.link(secret, root / "hard.txt")
    os.mkfifo(root / "pipe")
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    assert not service_env.workspace_files.listing()["items"]
    for name in ["link.txt", "hard.txt", "pipe", "escape/outside.txt"]:
        with pytest.raises(ValueError):
            service_env.workspace_files.preview("files", name)


@pytest.mark.parametrize("kind", ["agents", "workflows"])
def test_additional_root_alias_cannot_make_definition_folders_writable(service_env, kind):
    from nexus.core.studio_workspace_files import StudioWorkspaceFiles

    with pytest.raises(ValueError, match="read-only"):
        StudioWorkspaceFiles(
            service_env, roots={"alias": {"path": str(service_env.root / kind), "writable": True}}
        )


def test_listing_works_below_search_only_ancestor(tmp_path):
    workspace = tmp_path / "srv" / "project"
    (workspace / "files").mkdir(parents=True)
    (workspace / "files" / "a.txt").write_text("ok")
    service = StudioService(workspace)
    (tmp_path / "srv").chmod(0o311)  # VM layouts such as /srv/nexus at 0711
    try:
        items = service.workspace_files.listing("files")["items"]
    finally:
        (tmp_path / "srv").chmod(0o755)
    assert [item["name"] for item in items] == ["a.txt"]


def test_read_rejects_folder_symlink_swapped_after_configuration(service_env, tmp_path):
    files = service_env.root / "files"
    files.mkdir()
    (files / "sub").mkdir()
    (files / "sub").rmdir()
    (files / "sub").symlink_to(tmp_path)
    (tmp_path / "outside.txt").write_text("PRIVATE")
    with pytest.raises(ValueError):
        service_env.workspace_files.read("files", "sub/outside.txt")


def test_shared_file_permissions_and_private_snapshot_ownership(team):
    service, policy, _, request, _ = team
    assert request("/api/studio/assistant", user="viewer")[0] == 200
    assert (
        request(
            "/api/studio/assistant/answer", {"question": "Where are workflows?"}, user="viewer"
        )[0]
        == 200
    )
    assert request("/api/studio/files", user="viewer")[0] == 403
    assert request("/api/studio/files", user="bob")[0] == 403
    assert request("/api/studio/files")[0] == 401
    code, created = request(
        "/api/studio/files/upload",
        {
            "filename": "shared.txt",
            "content_base64": base64.b64encode(b"Workspace evidence").decode(),
        },
        user="alice",
    )
    assert code == 201
    assert request("/api/studio/files/preview?root=files&path=shared.txt", user="bob")[0] == 403
    code, saved = request(
        "/api/studio/files/snapshot",
        {"root": "files", "path": "shared.txt", "revision": created["revision"]},
        user="alice",
    )
    assert code == 201
    assert request(saved["download_url"], user="alice") == (200, b"Workspace evidence")
    assert request(saved["download_url"], user="bob")[0] == 403
    policy.bindings["alice"] = "operator"
    assert request("/api/studio/files", user="alice")[0] == 403


def test_assistant_ai_invokes_read_only_help_and_finishes_with_verified_context(tmp_path):
    seen = []
    service = None

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            seen.append(context)
            assert context.metadata["worker_tools"] == "none"
            if len(seen) == 1:
                name = service.assistant.spec()["tool_ids"][0]
                return AgentResult(
                    success=True,
                    output=json.dumps(
                        {
                            "tool_call": {
                                "name": name,
                                "arguments": {"question": "Where are agents saved?"},
                            }
                        }
                    ),
                )
            assert str(service.root / "agents") in context.prompt
            assert "PRIVATE FILE CONTENT" not in context.prompt
            return AgentResult(
                success=True,
                output='{"final":"Agents are YAML in the server workspace. Open Workspace files."}',
            )

    service = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        (tmp_path / "files").mkdir()
        (tmp_path / "files" / "private.txt").write_text("PRIVATE FILE CONTENT")
        result = wait(
            service,
            service.start_run(
                {"studio_assistant": True, "task": "Where are agents?", "worker_tools": "shell"}
            ),
        )
        assert result["status"] == "completed", result
        assert result.get("studio_assistant") is True
        assert service.session(result["session_id"]).get("studio_assistant") is True
        assert "server workspace" in result["output"]
        assert len(seen) == 2
        assert len(service.records("runs")) == 1
    finally:
        service.close()


def test_runtime_configuration_exposes_only_explicit_extra_roots(tmp_path):
    from nexus.core.studio_runtime import load_runtime_configuration

    root = tmp_path / "workspace"
    extra = tmp_path / "selected-documents"
    extra.mkdir()
    config = tmp_path / "runtime.json"
    config.write_text(json.dumps({"workspace_files": {"documents": {"path": str(extra)}}}))
    config.chmod(0o600)
    options = load_runtime_configuration(config, root)
    service = StudioService(root, **options)
    try:
        assert not service.workspace_files.roots["documents"]["writable"]
        assert "documents" in service.workspace_files.roots
    finally:
        service.close()


def test_listing_auto_creates_missing_writable_root(tmp_path):
    root = tmp_path / "fresh_workspace"
    root.mkdir()
    # files/ does not exist yet
    assert not (root / "files").exists()
    service = StudioService(root)
    try:
        res = service.workspace_files.listing("files")
        assert res["items"] == []
        assert (root / "files").is_dir()
    finally:
        service.close()


def test_assistant_file_tools_read_and_return_errors(service_env):
    import asyncio

    from nexus.tools import tool_registry

    (service_env.root / "files").mkdir(exist_ok=True)
    (service_env.root / "files" / "plan.md").write_text("Launch in November")
    names = {tid.rsplit(":", 1)[1]: tid for tid in service_env.assistant.spec()["tool_ids"]}
    listed = asyncio.run(tool_registry.call(names["workspace_list"], root="files"))
    assert [item["name"] for item in listed.output["items"]] == ["plan.md"]
    read = asyncio.run(tool_registry.call(names["workspace_read"], root="files", path="plan.md"))
    assert "Launch in November" in json.dumps(read.output)
    missing = asyncio.run(tool_registry.call(names["workspace_read"], root="files", path="nope.md"))
    assert missing.ok and "error" in missing.output


def test_workspace_write_is_a_proposal_until_applied(service_env):
    import asyncio

    from nexus.tools import tool_registry

    proposals = service_env.workspace_proposals
    files = service_env.root / "files"
    files.mkdir(exist_ok=True)
    names = {tid.rsplit(":", 1)[1]: tid for tid in service_env.assistant.spec()["tool_ids"]}
    created = asyncio.run(
        tool_registry.call(names["workspace_write"], path="notes/plan.md", content="v1")
    ).output
    assert "error" in created  # subfolder does not exist yet
    created = asyncio.run(
        tool_registry.call(names["workspace_write"], path="plan.md", content="v1")
    ).output
    assert created["status"] == "pending" and not (files / "plan.md").exists()
    applied = proposals.decide(created["proposal_id"], "apply")
    assert applied["status"] == "applied" and (files / "plan.md").read_text() == "v1"
    with pytest.raises(ValueError, match="already applied"):
        proposals.decide(created["proposal_id"], "apply")

    # Replacing needs the current revision; a stale proposal is refused at apply time.
    blind = asyncio.run(
        tool_registry.call(names["workspace_write"], path="plan.md", content="x")
    ).output
    assert "base_revision" in blind["error"]
    revision = service_env.workspace_files.preview("files", "plan.md")["revision"]
    update = proposals.propose("files", "plan.md", "v2", revision)
    stale = proposals.propose("files", "plan.md", "v3", revision)
    proposals.decide(update["proposal_id"], "apply")
    assert (files / "plan.md").read_text() == "v2"
    with pytest.raises(ValueError, match="changed since the proposal"):
        proposals.decide(stale["proposal_id"], "apply")
    assert (files / "plan.md").read_text() == "v2"
    assert not [p for p in files.iterdir() if p.name.endswith(".tmp")]

    rejected = proposals.propose("files", "other.md", "no")
    assert proposals.decide(rejected["proposal_id"], "reject")["status"] == "rejected"
    assert not (files / "other.md").exists()
    with pytest.raises(PermissionError):
        proposals.decide(
            proposals.propose("files", "x.md", "y")["proposal_id"], "apply", {"nexus_id": "other"}
        )
    with pytest.raises(PermissionError):
        proposals.propose("agents", "a.yaml", "id: a")


def test_proposals_list_by_run_and_owner(service_env):
    proposals = service_env.workspace_proposals
    proposals.propose("files", "a.md", "a", run_id="r1")
    proposals.propose("files", "b.md", "b", run_id="r2")
    assert [item["path"] for item in proposals.list("r1")["items"]] == ["a.md"]
    assert proposals.list("r1", {"nexus_id": "someone"})["items"] == []
