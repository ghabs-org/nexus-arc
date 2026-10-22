"""Explicit chat resubmission, separate from polling and uncertain-effect recovery."""

import copy
from urllib.parse import quote


class TerminalRetry:
    def __init__(self, client):
        self.client = client
        self.messages = {}

    def remember(self, payload, run):
        if payload.get("kind") != "chat" or run.get("status") != "completed":
            return
        selected = {
            key: payload[key]
            for key in ("kind", "task", "agent_id", "artifact_ids")
            if key in payload
        }
        selected.update(session_id=run["session_id"], provider=run["provider"])
        self.messages[run["session_id"]] = {
            "run_id": run["id"],
            "payload": copy.deepcopy(selected),
            "references": copy.deepcopy(run.get("artifact_refs", [])),
        }

    def prepare(self, session_id):
        saved = self.messages.get(session_id)
        if not saved:
            raise ValueError(
                "No completed message from this terminal to retry in the selected conversation"
            )
        if saved["payload"].get("artifact_ids") and (
            getattr(self.client, "capabilities", {}).get("expected_artifact_refs") is not True
            or [ref.get("id") for ref in saved["references"]] != saved["payload"]["artifact_ids"]
        ):
            raise ValueError("Studio must support attachment revision guards before retrying files")
        run = self.client.request("/api/runs/" + quote(saved["run_id"], safe=""))
        if (
            run.get("kind") != "chat"
            or run.get("session_id") != session_id
            or run.get("status") != "completed"
            or run.get("awaiting_approval")
            or run.get("awaiting_input")
        ):
            raise ValueError(
                "Inspect Activity; pending, failed or uncertain runs cannot be resubmitted with /retry"
            )
        effects = self.client.request("/api/workbench/effects")["items"]
        if any(effect.get("run_id") == saved["run_id"] for effect in effects):
            raise ValueError(
                "This run has recorded external actions; use its existing review/recovery controls in Studio"
            )
        payload = copy.deepcopy(saved["payload"])
        payload["expected_artifact_refs"] = copy.deepcopy(saved["references"])
        return payload
