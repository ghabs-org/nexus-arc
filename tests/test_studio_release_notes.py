"""Release history is usable offline and cannot masquerade as installed-version proof."""

import json
import plistlib
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core import studio_release_notes as notes
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler
from tests.test_studio_team import wait


def test_versioned_history_matches_source_and_preserves_historical_evidence_limits():
    data = notes.release_history()
    root = Path(__file__).resolve().parents[1]
    version = json.loads((root / "apps/studio-desktop/src-tauri/tauri.conf.json").read_text())[
        "version"
    ]
    assert data["source_version"] == version
    assert notes.require_release_notes(version)
    entries = {entry["version"]: entry for entry in data["entries"]}
    assert entries["1.0.1"]["status"] == "unverified"
    assert entries["1.0.4"]["status"] == "local-install"
    assert entries["1.0.6"]["date"] is None
    assert entries["Unreleased"]["status"] == "unreleased"
    for entry in entries.values():
        for evidence in entry["evidence"]:
            if not evidence.startswith("git:") and "local artifact record" not in evidence:
                assert (root / evidence).is_file(), evidence


def test_unknown_or_fixture_versions_cannot_pass_release_gate():
    for version in ("1.0.1", "99.0.0", "Unreleased"):
        with pytest.raises(ValueError, match="source-controlled Studio release notes"):
            notes.require_release_notes(version)
    documented = notes.require_release_notes("1.0.5")
    assert "conversation loading" in documented.lower()
    assert "public update feed is unpublished" in documented


def test_macos_release_stops_before_signing_an_undocumented_version(tmp_path, monkeypatch):
    from nexus.core import studio_desktop_release as release

    app = tmp_path / "Nexus Studio.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "com.nexusarc.studio",
                "CFBundleShortVersionString": "99.0.0",
                "CFBundleExecutable": "studio",
            }
        )
    )
    key, cli = tmp_path / "test-key", tmp_path / "test-cli"
    key.touch()
    cli.touch()
    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setattr(release, "developer_identities", lambda: [("fixture", "fixture")])

    def no_external_operation(_):
        raise AssertionError("Signing/building must not begin without release notes")

    monkeypatch.setattr(release, "_run", no_external_operation)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="source-controlled Studio release notes"):
        release.release_macos(app, output, "fixture", "fixture", key, cli)
    assert not output.exists()


def test_release_lookup_does_not_borrow_features_from_another_version():
    result = notes.release_answer("What changed in 99.0.0?")
    assert result["release_notes"]["entries"] == []
    assert "No recorded release notes for 99.0.0" in result["answer"]
    assert "Built-in desktop model router" not in result["answer"]
    old = notes.release_answer("What changed in version 1.0.2?")
    assert [e["version"] for e in old["release_notes"]["entries"]] == ["1.0.2"]
    assert old["sources"][0]["url"] == "/release-notes.html#1.0.2"
    assert "Built-in desktop model router" not in old["answer"]


def test_release_gate_rejects_duplicate_versions(tmp_path, monkeypatch):
    data = notes.release_history()
    data["entries"].append(data["entries"][1])
    path = tmp_path / "notes.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(notes, "CATALOG", path)
    with pytest.raises(ValueError, match="unique"):
        notes.require_release_notes("1.0.6")


def test_local_assistant_answers_new_features_without_provider_or_workspace_reads(tmp_path):
    def no_provider(_):
        raise AssertionError("Local release history must not invoke a provider")

    service = StudioService(tmp_path, provider_factory=no_provider)
    try:
        result = service.assistant.answer("Which are the new features of Studio?")
        assert result["mode"] == "local-help"
        assert result["release_notes"]["installed_version"] is None
        assert "Unreleased" in result["answer"] and "1.0.6" in result["answer"]
        assert "not the version installed" in result["answer"]
        assert "release-notes.html" in result["sources"][0]["url"]
        assert "Bundled release history" in service.assistant.spec()["purpose"]
        assert "release_notes" not in service.assistant.answer("Where are workflows saved?")
        service.team = SimpleNamespace(bindings={}, manager=None)
        with pytest.raises(PermissionError, match="project access"):
            service.assistant.answer("What is new?", {"nexus_id": "outsider"})
    finally:
        service.team = None
        service.close()


def test_ai_help_tool_receives_exact_historical_version_and_citable_source(tmp_path):
    seen = []
    service = None

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            seen.append(context)
            if len(seen) == 1:
                return AgentResult(
                    success=True,
                    output=json.dumps(
                        {
                            "tool_call": {
                                "name": service.assistant.spec()["tool_ids"][0],
                                "arguments": {"question": "What changed in Studio 1.0.4?"},
                            }
                        }
                    ),
                )
            assert "local-install" in context.prompt
            assert "No new DMG was produced" in context.prompt
            assert "/release-notes.html#1.0.4" in context.prompt
            return AgentResult(
                success=True,
                output=json.dumps(
                    {
                        "final": "Studio 1.0.4 added verified provider status. It was a local install, with no DMG.",
                    }
                ),
            )

    service = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        run = wait(
            service,
            service.start_run(
                {
                    "kind": "chat",
                    "studio_assistant": True,
                    "task": "What changed in Studio 1.0.4?",
                }
            ),
        )
        assert run["status"] == "completed", run
        assert len(seen) == 2
    finally:
        service.close()


def test_bundled_release_page_and_catalog_are_served_offline(tmp_path):
    service = StudioService(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(service, "fixture"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        for path in ("/release-notes.html", "/release-notes.js", "/release-notes.json"):
            with urllib.request.urlopen(origin + path, timeout=5) as response:
                assert response.status == 200
                assert response.read()
        with urllib.request.urlopen(
            origin + "/api/studio/assistant?question=version%201.0.5"
        ) as response:
            result = json.load(response)
            assert result["release_notes"]["entries"][0]["version"] == "1.0.5"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
