"""Terminal consumer of existing Studio conversations, tasks and authorization."""

import time
from urllib.parse import quote

from nexus.core.studio_http_client import StudioHTTPClient


def wait_for_run(client, run_id, timeout):
    deadline = time.monotonic() + timeout
    while True:
        run = client.request("/api/runs/" + quote(run_id, safe=""))
        if run["status"] not in {"queued", "running"}:
            return run
        if time.monotonic() >= deadline:
            raise ValueError(
                f"Run {run_id} still pending. Inspect Activity; do not resubmit the task."
            )
        time.sleep(0.25)


def chat_command(args):
    if not 1 <= args.timeout <= 3600:
        raise ValueError("Chat timeout must be 1–3600 seconds")
    client = StudioHTTPClient(args.url, args.session_env)
    session_id = args.session_id
    try:
        if args.run_id:
            run = wait_for_run(client, args.run_id, args.timeout)
            print(run.get("output") or run.get("error") or f"Run {run['id']}: {run['status']}")
            return
        task = " ".join(args.message).strip()
        while True:
            if not task:
                try:
                    task = input("You: ").strip()
                except EOFError:
                    return
                if task in {"/exit", "/quit"}:
                    return
                if not task:
                    continue
            payload = {"kind": "chat", "task": task}
            for key, value in {
                "session_id": session_id,
                "agent_id": args.agent,
                "provider": args.provider,
            }.items():
                if value:
                    payload[key] = value
            # Only this explicit user message causes submission. Polling and
            # reconnecting never submit it again, including after timeouts.
            run = client.request("/api/runs", payload)
            session_id = run.get("session_id")
            print(f"Session {session_id} · Run {run['id']}")
            run = wait_for_run(client, run["id"], args.timeout)
            if run["status"] == "completed":
                print(run["output"])
                for citation in run.get("citations", []):
                    print(
                        "Source: "
                        + (
                            citation.get("url")
                            or f"{citation['filename']} · revision {citation['artifact_revision']} · page {citation['page']}"
                        )
                    )
            else:
                print(run.get("error") or f"Run {run['id']}: {run['status']}. Review it in Studio.")
                return
            if args.message:
                return
            task = ""
    finally:
        client.close()
