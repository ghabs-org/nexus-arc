"""Terminal consumer of existing Studio conversations, tasks and authorization."""

import json
import time
from urllib.parse import quote

from nexus.core.studio_http_client import StudioHTTPClient
from nexus.core.studio_terminal_sessions import TerminalSessions
from nexus.core.studio_terminal_ui import TerminalInput, render_markdown


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
    plain = getattr(args, "plain", False)
    new_session = getattr(args, "new_session", False)
    if new_session and (args.session_id or args.run_id):
        raise ValueError("--new-session cannot be combined with --session-id or --run-id")
    if len(artifacts) > 5:
        raise ValueError("Attach at most five saved Studio files")
    if args.run_id and artifacts:
        raise ValueError("Run inspection cannot submit attachments")
    if structured and not (args.run_id or " ".join(args.message).strip()):
        raise ValueError("JSON output requires a message or --run-id")
    client = StudioHTTPClient(args.url, args.session_env)
    sessions = TerminalSessions(client, args.session_id, artifacts)
    sessions.plain = plain
    try:
        if args.run_id:
            run = wait_for_run(client, args.run_id, args.timeout)
            print_result(run, structured=structured, plain=plain)
            return
        task = " ".join(args.message).strip()
        if not task:
            if args.session_id:
                sessions.select(sessions.current())
            elif new_session:
                sessions.index.select(None)
            else:
                sessions.restore()
        terminal = TerminalInput(sessions, plain=plain) if not task else None
        sessions.enhanced_input = terminal is not None and terminal.prompt_session is not None
        while True:
            if not task:
                try:
                    task = terminal.read().strip()
                except (EOFError, KeyboardInterrupt):
                    return
                if task in {"/exit", "/quit"}:
                    return
                if not task:
                    continue
                if sessions.dispatch(task):
                    if sessions.exit_requested:
                        return
                    task, sessions.pending_task = sessions.pending_task, ""
                    if not task:
                        continue
            payload = {"kind": "chat", "task": task}
            if sessions.pending_title and not sessions.session_id:
                payload["session_title"] = sessions.pending_title
            if sessions.artifact_ids:
                payload["artifact_ids"] = sessions.artifact_ids
            for key, value in {
                "session_id": sessions.session_id,
                "agent_id": sessions.pending_agent_id or args.agent,
                "provider": args.provider,
            }.items():
                if value:
                    payload[key] = value
            if sessions.pending_retry is not None:
                payload, sessions.pending_retry = sessions.pending_retry, None
            # Only this explicit user message causes submission. Polling and
            # reconnecting never submit it again, including after timeouts.
            run = client.request("/api/runs", payload)
            sessions.pending_agent_id = None
            sessions.session_id = run.get("session_id")
            if not structured:
                print(f"Session {sessions.session_id} · Run {run['id']}")
            if terminal is not None:
                sessions.accepted(run)
            run = wait_for_run(client, run["id"], args.timeout)
            print_result(run, structured=structured, plain=plain)
            if run["status"] != "completed":
                return
            sessions.retry.remember(payload, run)
            sessions.artifact_ids = []
            if args.message:
                return
            task = ""
    finally:
        client.close()


def print_result(run, *, structured=False, plain=False):
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
    if run.get("output"):
        render_markdown(run["output"], plain=plain)
    else:
        print(run.get("error") or f"Run {run['id']}: {run['status']}")
    for citation in run.get("citations", []):
        reference = citation.get("url")
        if not reference:
            reference = citation.get("filename") or citation.get("id") or "Saved source"
            if citation.get("artifact_revision") is not None:
                reference += f" · revision {citation['artifact_revision']}"
            if citation.get("page") is not None:
                reference += f" · page {citation['page']}"
        print("Source: " + reference)
