"""Full-app feedback acceptance using disposable team identities and local fixtures.

Run: PYTHONPATH=. python tests/studio_feedback_acceptance.py
Visit the printed /acceptance link to sign in as Alice, Bob, Viewer or Owner.
The loopback front door and production team routes come from the sharing fixture;
TLS and real identity-provider acceptance remain separate.
"""

import json
import tempfile
from http.server import ThreadingHTTPServer

from tests.studio_sharing_team_acceptance import create_fixture
from tests.test_studio_retrieval_evidence import generated


def create_feedback_fixture(root):
    studio, handler, _ = create_fixture(root)
    studio.save_definition(
        "agents",
        {"name": "Feedback reviewer", "instructions": "Review the submitted synthetic policy."},
    )
    run, _ = generated(studio, "alice")
    studio._update_run(run["id"], output="Returns are allowed within **thirty days**.")
    for identity, owner in [("feedback-new", "alice"), ("feedback-bob", "bob")]:
        studio._write(
            studio._path("runs", identity),
            {
                "id": identity,
                "kind": "chat",
                "provider": "codex",
                "status": "completed",
                "requester_nexus_id": owner,
                "task": "Summarize the synthetic policy",
                "output": "**Approved** for testing.",
                "structured_content": [],
                "created_at": "2026-10-23T10:00:00+00:00",
                "updated_at": "2026-10-23T10:00:00+00:00",
            },
        )
    studio.collaboration.feedback(
        {"run_id": run["id"], "rating": 1, "comment": "Initial note\nKeep formatting."},
        {"nexus_id": "alice", "role": "builder"},
    )
    studio.collaboration.feedback(
        {"run_id": "feedback-bob", "rating": -1, "comment": "BOB_PRIVATE_COMMENT"},
        {"nexus_id": "bob", "role": "operator"},
    )
    session = studio.session("sharing-demo")
    session.update(
        title="Feedback acceptance",
        messages=[
            {"role": "user", "content": "Return policy?"},
            {
                "role": "assistant",
                "content": "Returns are allowed within **thirty days**.",
                "run_id": run["id"],
            },
            {"role": "user", "content": "Summarize the synthetic policy"},
            {"role": "assistant", "content": "**Approved** for testing.", "run_id": "feedback-new"},
        ],
    )
    studio._write(studio._path("sessions", "sharing-demo"), session)
    return studio, handler


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-feedback-acceptance-") as root:
        studio, handler = create_feedback_fixture(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/acceptance"}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            studio.close()


if __name__ == "__main__":
    main()
