"""Versioned, bounded conversation content over the existing private run records."""

import json
import uuid

TEXT_LIMIT = 1024 * 1024


class StudioConversations:
    def __init__(self, studio):
        self.studio = studio

    def begin(self, run_id, invocation_id):
        with self.studio.lock:
            self.studio._update_run(
                run_id,
                output="",
                stream={
                    "version": 1,
                    "invocation_id": invocation_id,
                    "sequence": 0,
                    "status": "streaming",
                    "truncated": False,
                },
            )

    def delta(self, run_id, text, invocation_id=None):
        if not isinstance(text, str):
            raise ValueError("Stream content must be text")
        with self.studio.lock:
            run = self.studio.run(run_id)
            stream = dict(run.get("stream") or {})
            if invocation_id and (
                stream.get("invocation_id") != invocation_id or stream.get("status") != "streaming"
            ):
                return  # Late callback from a finished invocation cannot overwrite another.
            previous = run.get("output", "")
            remaining = max(0, TEXT_LIMIT - len(previous))
            stream.update(
                version=1,
                sequence=stream.get("sequence", 0) + 1,
                status="streaming",
                truncated=stream.get("truncated", False) or len(text) > remaining,
            )
            self.studio._update_run(run_id, output=previous + text[:remaining], stream=stream)

    def finish(self, run_id, invocation_id, result=None):
        with self.studio.lock:
            run = self.studio.run(run_id)
            stream = dict(run.get("stream") or {})
            if stream.get("invocation_id") != invocation_id:
                return
            stream.update(
                sequence=stream.get("sequence", 0) + 1,
                status="completed"
                if result is not None and result.success
                else "interrupted-or-failed",
            )
            # Provider's final answer is authoritative: tool turns and stream
            # revisions must not remain concatenated into the committed answer.
            values = {"stream": stream}
            if result is not None and result.success:
                values["output"] = result.output
            self.studio._update_run(run_id, **values)

    def content(self, result, payload):
        """Accept optional typed parts; keep plain output and drop unsafe metadata."""
        raw = (result.metadata or {}).get("structured_content")
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > 40:
            return []
        actor = {
            "nexus_id": payload.get("requester_nexus_id"),
            "role": self.studio.team.bindings.get(payload.get("requester_nexus_id"), "operator")
            if self.studio.team
            else "operator",
        }
        parts = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind in {"text", "markdown"} and isinstance(item.get("text"), str):
                parts.append({"type": kind, "text": item["text"][:262144]})
            elif kind == "json":
                try:
                    encoded = json.dumps(item.get("data"), allow_nan=False)
                    if len(encoded) <= 128000:
                        parts.append({"type": "json", "data": json.loads(encoded)})
                except (ValueError, TypeError, OverflowError, RecursionError):
                    continue
            elif kind == "artifact":
                try:
                    current = self.studio.catalog.get("artifacts", item.get("artifact_id"))
                    self.studio.collaboration.owned(current, actor if self.studio.team else None)
                    revision = item.get("revision", current["revision"])
                    if type(revision) is not int or revision < 1:
                        continue
                    artifact = self.studio.artifacts.revision(current["id"], revision)
                    self.studio.collaboration.owned(artifact, actor if self.studio.team else None)
                    parts.append(
                        {
                            "type": "artifact",
                            "artifact_id": artifact["id"],
                            "revision": revision,
                            "filename": artifact["data"]["filename"],
                        }
                    )
                except (ValueError, FileNotFoundError, PermissionError):
                    continue
        # Bound the whole payload as well as individual parts.
        while parts and len(json.dumps(parts, ensure_ascii=False)) > 262144:
            parts.pop()
        return parts

    @staticmethod
    def invocation_id():
        return uuid.uuid4().hex
