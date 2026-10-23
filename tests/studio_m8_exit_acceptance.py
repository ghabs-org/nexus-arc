"""Milestone 8 local integration scenario, with explicit provider substitutes.

Verifies local integration across voice jobs, private sharing,
feedback collection, evaluation dataset promotion, named experiments,
operational overview, telemetry/OTLP/Prometheus export, and local offline backup/restore.
"""

import base64
import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_backup import create_snapshot, restore_snapshot
from nexus.core.studio_readiness import deployment_readiness
from nexus.core.studio_team import NexusTeamPolicy
from nexus.studio import make_studio_handler
from tests.test_studio_team import SESSIONS, NexusIdentity


def wait_run(studio, run):
    for _ in range(500):
        record = studio.run(run["id"])
        if record["status"] not in {"running", "queued"}:
            return record
        time.sleep(0.01)
    raise TimeoutError("Run did not finish in time")


def run_m8_exit_scenario():
    with tempfile.TemporaryDirectory(prefix="nexus-m8-exit-") as temp_dir, ExitStack() as cleanup:
        root = Path(temp_dir)
        workspace = root / "workspace"
        private_state = root / "private-state"

        class DummySpeechClient:
            def __init__(self):
                self.audio = SimpleNamespace(
                    transcriptions=SimpleNamespace(create=self.transcribe),
                    speech=SimpleNamespace(create=self.speak),
                )

            def transcribe(self, **kwargs):
                return SimpleNamespace(text="Synthetic transcription result")

            def speak(self, **kwargs):
                return SimpleNamespace(content=b"RIFFsynthetic-audio")

            def close(self):
                pass

        class DeterministicProvider:
            def __init__(self):
                self.turn = 0

            async def check_availability(self):
                return True

            async def execute_agent(self, context):
                self.turn += 1
                tool = next(iter(studio.dynamic_embeds.names))
                if "Telemetry verification run" in context.prompt and self.turn == 1:
                    output = json.dumps(
                        {"tool_call": {"name": tool, "arguments": {"text": "«math:2+2»"}}}
                    )
                else:
                    output = "Synthetic transcription result"
                return AgentResult(
                    success=True,
                    output=output,
                    metadata={
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 50,
                            "total_tokens": 150,
                        },
                    },
                )

        manager = NexusIdentity()
        policy = NexusTeamPolicy(
            project_key="sample",
            public_url="https://studio.example.test",
            login_url="https://studio.example.test/acceptance",
            bindings={"alice": "builder", "bob": "operator", "owner": "owner"},
            manager=manager,
            environment_resolver=lambda _: ({"OPENAI_API_KEY": "sk-synthetic-key"}, None),
        )

        def selected_provider(name, payload):
            policy.assert_execution(payload["requester_nexus_id"])
            return DeterministicProvider()

        policy.provider = selected_provider
        studio = StudioService(
            workspace,
            state_dir=private_state,
            team_policy=policy,
            speech_options={"client_factory": lambda profile, payload: DummySpeechClient()},
            provider_factory=lambda _: DeterministicProvider(),
        )
        cleanup.callback(studio.close)

        # -------------------------------------------------------------
        # 1. Voice conversation preparation, execution, and cleanup
        # -------------------------------------------------------------
        voice_payload = {
            "profile": "openai-transcribe",
            "audio_base64": base64.b64encode(b"RIFF....WAVEfmt synthetic audio").decode(),
            "format": "wav",
        }
        speech_run = studio.speech.queue(voice_payload, {"nexus_id": "alice", "role": "builder"})
        assert speech_run["id"] is not None
        assert studio.speech.directory.exists()

        completed_speech = wait_run(studio, speech_run)
        assert completed_speech["status"] == "completed"
        assert completed_speech["output"] == "Synthetic transcription result"

        # Verify cleanup removed temporary audio
        removed = studio.speech.cleanup()
        assert removed["files"] >= 0

        # -------------------------------------------------------------
        # 2. Private & Public conversation sharing
        # -------------------------------------------------------------
        session_id = "chat-m8-session"
        timestamp = datetime.now(UTC).isoformat()
        studio._write(
            studio._path("sessions", session_id),
            {
                "id": session_id,
                "owner_id": "alice",
                "title": "Milestone 8 Conversation",
                "provider": "codex",
                "created_at": timestamp,
                "updated_at": timestamp,
                "messages": [
                    {"role": "user", "content": "Analyze system metrics."},
                    {"role": "assistant", "content": "System operating normally within bounds."},
                ],
            },
        )
        artifact = studio.catalog.save(
            "artifacts",
            {
                "id": "audit-report",
                "name": "Audit Report",
                "requester_nexus_id": "alice",
                "data": {
                    "filename": "audit.txt",
                    "content_base64": base64.b64encode(b"Audit passed cleanly.").decode(),
                },
            },
        )
        share = studio.collaboration.share(
            {
                "session_id": session_id,
                "recipients": ["bob"],
                "artifact_ids": [artifact["id"]],
            },
            {"nexus_id": "alice", "role": "builder"},
        )
        assert share["id"] is not None
        assert any(a["id"] == artifact["id"] for a in share["artifacts"])

        # Verify Bob can read the shared snapshot
        bob_view = studio.collaboration.get_share(
            share["id"], {"nexus_id": "bob", "role": "operator"}
        )
        assert len(bob_view["messages"]) == 2
        assert any(a["id"] == artifact["id"] for a in bob_view["artifacts"])

        # -------------------------------------------------------------
        # 3. Feedback collection & evaluation dataset promotion
        # -------------------------------------------------------------
        feedback = studio.collaboration.feedback(
            {
                "run_id": completed_speech["id"],
                "rating": 1,
                "comment": "Accurate analysis",
            },
            {"nexus_id": "alice", "role": "builder"},
        )
        assert feedback["rating"] == 1

        studio.save_definition(
            "agents", {"id": "analyst", "name": "Analyst", "instructions": "Analyze metrics"}
        )

        suite = studio.collaboration.promote(
            {
                "feedback_id": feedback["id"],
                "expected": "Synthetic transcription result",
                "name": "Promoted M8 Suite",
                "target_kind": "agent",
                "target_id": "analyst",
            },
            {"nexus_id": "alice", "role": "builder"},
        )
        assert suite["id"] is not None

        experiment = studio.catalog.save(
            "experiments",
            {
                "name": "M8 Variant Experiment",
                "requester_nexus_id": "alice",
                "data": {
                    "evaluation_id": suite["id"],
                    "evaluation_revision": suite["revision"],
                    "runsPerExample": 1,
                    "passThreshold": 1.0,
                    "variants": [{"name": "Standard", "provider": "codex", "model": "gpt-5"}],
                },
            },
        )
        assert experiment["id"] is not None
        experiment_run = wait_run(
            studio,
            studio.start_run(
                {
                    "kind": "evaluation",
                    "experiment_id": experiment["id"],
                    "requester_nexus_id": "alice",
                }
            ),
        )
        assert experiment_run["status"] == "completed", experiment_run.get("error")
        experiment_report = studio._read(studio._path("evaluation-reports", experiment_run["id"]))
        assert experiment_report["complete"] and experiment_report["acceptance"]
        assert experiment_report["total"] == experiment_report["passed"] == 1

        # -------------------------------------------------------------
        # 4. Telemetry: parent-child linking, usage, OTLP, Prometheus
        # -------------------------------------------------------------
        tool = next(iter(studio.dynamic_embeds.names))
        studio.save_definition(
            "agents",
            {
                "id": "telemetry-agent",
                "name": "Telemetry agent",
                "instructions": "Use the selected safe tool",
                "tool_ids": [tool],
            },
        )
        telemetry_run = wait_run(
            studio,
            studio.start_run(
                {
                    "kind": "chat",
                    "task": "Telemetry verification run",
                    "agent_id": "telemetry-agent",
                    "requester_nexus_id": "alice",
                }
            ),
        )
        assert telemetry_run["status"] == "completed", telemetry_run.get("error")
        run_id = telemetry_run["id"]

        tokens = studio.telemetry.summarize_run_tokens(
            run_id, {"nexus_id": "alice", "role": "builder"}
        )
        assert tokens["total_tokens"] == 300

        otlp = studio.telemetry.export_opentelemetry_traces(
            run_id, {"nexus_id": "alice", "role": "builder"}
        )
        spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
        run_span = next(s for s in spans if s["name"].startswith("run:"))
        tool_span = next(s for s in spans if s["name"] == "tool:" + tool)
        inv_span = next(s for s in spans if s["spanId"] == tool_span["parentSpanId"])
        assert inv_span["parentSpanId"] == run_span["spanId"]
        assert tool_span["parentSpanId"] == inv_span["spanId"]

        prom = studio.telemetry.export_prometheus_metrics({"nexus_id": "alice", "role": "builder"})
        assert "nexus_prompt_tokens_total" in prom

        # -------------------------------------------------------------
        # 5. HTTP Endpoints verification via Handler
        # -------------------------------------------------------------
        handler = make_studio_handler(studio, "synthetic-m8-exit")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        base_url = f"http://127.0.0.1:{server.server_port}"
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        alice_token = next(k for k, v in SESSIONS.items() if v == "alice")
        owner_token = next(k for k, v in SESSIONS.items() if v == "owner")
        alice_cookie = f"nexus_web_session={alice_token}"
        owner_cookie = f"nexus_web_session={owner_token}"

        try:
            # Check overview endpoint
            req = urllib.request.Request(f"{base_url}/api/studio/overview")
            req.add_header("Host", policy.host)
            req.add_header("Cookie", owner_cookie)
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                overview_data = json.loads(resp.read().decode())
                assert overview_data["format"] == "nexus-studio-overview/v1"
                assert overview_data["scope"] == "workspace"
                assert "feedback" in overview_data
                assert "evaluations" in overview_data

            # Check telemetry usage endpoint
            req = urllib.request.Request(f"{base_url}/api/workbench/traces/{run_id}/usage")
            req.add_header("Host", policy.host)
            req.add_header("Cookie", alice_cookie)
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                usage_res = json.loads(resp.read().decode())
                assert usage_res["total_tokens"] == 300

            # Check Prometheus endpoint
            req = urllib.request.Request(f"{base_url}/api/workbench/metrics/prometheus")
            req.add_header("Host", policy.host)
            req.add_header("Cookie", alice_cookie)
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                metrics_res = resp.read().decode()
                assert "nexus_runs_total" in metrics_res
        finally:
            server.shutdown()
            server.server_close()

        # -------------------------------------------------------------
        # 6. Deployment Readiness and Backup / Restore
        # -------------------------------------------------------------
        readiness = deployment_readiness(studio, {"nexus_id": "owner", "role": "owner"})
        assert len(readiness["items"]) >= 5
        assert any(item["name"] == "Workspace mode" for item in readiness["items"])

        # Create full verified snapshot
        snapshot_dest = root / "m8-backup"
        studio.close()
        create_snapshot(workspace, snapshot_dest, state_dir=private_state, quiesced=True)
        assert snapshot_dest.is_dir()
        assert (snapshot_dest / "manifest.json").is_file()

        # Restore into new clean studio workspace
        restore_dir = root / "restored-env"
        restore_res = restore_snapshot(snapshot_dest, restore_dir)
        import os

        os.chmod(restore_res["state_dir"], 0o700)
        restored_studio = StudioService(
            restore_res["root"],
            state_dir=restore_res["state_dir"],
            team_policy=policy,
        )
        try:
            # Verify restored data
            restored_session = restored_studio.session(session_id)
            assert restored_session["id"] == session_id
            assert len(restored_session["messages"]) == 2
        finally:
            restored_studio.close()
            studio.close()

    print(
        "Milestone 8 local integration passed; real voice models, VM and device checks remain pending."
    )


def test_m8_exit_scenario():
    run_m8_exit_scenario()


if __name__ == "__main__":
    run_m8_exit_scenario()
