"""Keep completed builder responses separate from fallible local draft processing."""

from __future__ import annotations

import json

FINALIZE_STEP = "__build_finalize__"


class StudioBuildRecovery:
    def __init__(self, builder):
        self.builder = builder
        self.studio = builder.studio

    def checkpoint(self, run_id):
        studio = self.studio
        run = studio.run(run_id)
        if run.get("kind") != "build":
            return None
        path = studio._path("build-responses", run_id)
        if path.exists():
            saved = studio._read(path)
            if (
                saved.get("run_id") == run_id
                and saved.get("build_id") == run.get("build_id")
                and (saved.get("owner_id") or "desktop")
                == (run.get("requester_nexus_id") or "desktop")
            ):
                return saved
            raise ValueError("Saved build response does not match this run")
        # Older streaming builds already retain an authoritative final response.
        # Partial text, failed invocations and unrelated traces are never checkpoints.
        stream = run.get("stream") or {}
        if (
            stream.get("status") != "completed"
            or not stream.get("invocation_id")
            or not run.get("output")
        ):
            return None
        trace_path = studio._path("invocation-traces", stream.get("invocation_id"))
        if not trace_path.exists():
            return None
        trace = studio._read(trace_path)
        if (
            trace.get("run_id") != run_id
            or trace.get("status") != "completed"
            or trace.get("agent") != "quick-build"
            or (trace.get("owner_id") or "desktop") != (run.get("requester_nexus_id") or "desktop")
        ):
            return None
        request = studio.retry.request(run_id)
        if not request:
            return None
        payload = request["payload"]
        self.builder.validate_request(payload)
        path = studio._path("builds", payload["build_id"])
        return self.response(
            run_id,
            payload,
            run["output"],
            studio._read(path) if path.exists() else None,
            run.get("build_corrections", 0),
        )

    def response(self, run_id, payload, raw, previous, corrections):
        return {
            "id": run_id,
            "run_id": run_id,
            "owner_id": payload.get("requester_nexus_id"),
            "build_id": payload["build_id"],
            "raw": raw,
            "previous": previous,
            "corrections": corrections,
        }

    def remember(self, run_id, payload, raw, previous, corrections):
        saved = self.response(run_id, payload, raw, previous, corrections)
        self.studio._write(self.studio._path("build-responses", run_id), saved)
        return saved

    def validate(self, payload, saved):
        path = self.studio._path("builds", payload["build_id"])
        current = self.studio._read(path) if path.exists() else None
        if current != saved["previous"] and ("draft" not in saved or current != saved["draft"]):
            raise ValueError("Build changed. Reopen its latest revision before retrying.")

    def parse(self, raw, payload):
        raw = raw.strip()
        if len(raw.encode("utf-8")) > 262144:
            raise ValueError("Builder proposal exceeds 256 KiB; request a smaller build")
        if raw.startswith("```json\n") and raw.endswith("```"):
            raw = raw[8:-3].strip()
        elif raw.startswith("```\n") and raw.endswith("```"):
            raw = raw[4:-3].strip()
        return self.builder.normalize(
            json.loads(raw),
            payload["build_id"],
            payload.get("provider") or "codex",
            payload.get("model") or "",
        )

    def finish(self, run_id, payload, saved, proposal=None):
        studio = self.studio
        studio.execution.record(
            run_id,
            "node",
            node_id=FINALIZE_STEP,
            node_name="Validate and save build",
            phase="build-finalize",
            status="running",
        )
        try:
            # A retry deliberately has no provider factory, availability check or correction pass.
            proposal = self.parse(saved["raw"], payload) if proposal is None else proposal
            with studio.lock:
                self.validate(payload, saved)
                previous = saved["previous"]
                from nexus.core.studio import _now

                now = _now()
                draft = saved.get("draft") or {
                    "id": payload["build_id"],
                    "owner_id": payload.get("requester_nexus_id"),
                    "status": "draft",
                    "revision": previous["revision"] + 1 if previous else 1,
                    "provider": payload.get("provider") or "codex",
                    "model": payload.get("model") or "",
                    "effort": payload.get("effort") or "",
                    "worker_tools": payload.get("worker_tools") or "web",
                    "proposal": proposal,
                    "messages": (previous["messages"] if previous else [])
                    + [
                        {"role": "user", "content": payload["task"]},
                        {"role": "assistant", "content": proposal["summary"]},
                    ],
                    "created_at": previous["created_at"] if previous else now,
                    "updated_at": now,
                }
                if draft["proposal"] != proposal:
                    raise ValueError(
                        "Build dependencies changed. Review the saved response before retrying."
                    )
                if previous:
                    for field in ("base_definitions", "base_resources", "base_checksums"):
                        if field in previous:
                            draft[field] = previous[field]
                # Pin the exact draft before writing, so a late failure cannot append another revision.
                saved = {**saved, "draft": draft}
                studio._write(studio._path("build-responses", run_id), saved)
                studio._write(studio._path("builds", payload["build_id"]), draft)
                studio._update_run(
                    run_id,
                    phase="draft-ready",
                    build_corrections=saved["corrections"],
                )
        except BaseException:
            studio.execution.record(
                run_id,
                "node",
                node_id=FINALIZE_STEP,
                node_name="Validate and save build",
                phase="build-finalize",
                status="failed",
            )
            raise
        studio.execution.record(
            run_id,
            "node",
            node_id=FINALIZE_STEP,
            node_name="Validate and save build",
            phase="build-finalize",
            status="completed",
        )
        return proposal["summary"]
