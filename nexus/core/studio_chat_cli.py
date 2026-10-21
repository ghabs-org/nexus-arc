"""Terminal consumer of existing Studio conversations, tasks and authorization."""

import json
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
    artifacts = list(dict.fromkeys(getattr(args, "artifact_id", []) or []))
    structured = getattr(args, "json_output", False)
    if len(artifacts) > 5:
        raise ValueError("Attach at most five saved Studio files")
    if args.run_id and artifacts:
        raise ValueError("Run inspection cannot submit attachments")
    if structured and not (args.run_id or " ".join(args.message).strip()):
        raise ValueError("JSON output requires a message or --run-id")
    client = StudioHTTPClient(args.url, args.session_env)
    session_id = args.session_id
    try:
        if args.run_id:
            run = wait_for_run(client, args.run_id, args.timeout)
            print_result(run, structured=structured)
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
            if artifacts:
                payload["artifact_ids"] = artifacts
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
            if not structured:
                print(f"Session {session_id} · Run {run['id']}")
            run = wait_for_run(client, run["id"], args.timeout)
            print_result(run, structured=structured)
            if run["status"] != "completed":
                return
            artifacts = []
            if args.message:
                return
            task = ""
    finally:
        client.close()


def print_result(run, *, structured=False):
    """Render the same terminal result for submission and read-only recovery.

    Structured output deliberately selects public result fields, rather than
    dumping the run's internal execution/configuration records.
    """
    if structured:
        print(
            json.dumps(
                {
                    "format": "nexus-studio-chat-result/v1",
                    **{
                        key: run.get(key)
                        for key in ("id", "session_id", "status", "output", "error")
                    },
                    "citations": run.get("citations", []),
                    "artifact_refs": run.get("artifact_refs", []),
                },
                ensure_ascii=False,
            )
        )
        return
    print(run.get("output") or run.get("error") or f"Run {run['id']}: {run['status']}")
    for citation in run.get("citations", []):
        reference = citation.get("url")
        if not reference:
            reference = citation.get("filename") or citation.get("id") or "Saved source"
            if citation.get("artifact_revision") is not None:
                reference += f" · revision {citation['artifact_revision']}"
            if citation.get("page") is not None:
                reference += f" · page {citation['page']}"
        print("Source: " + reference)
