"""Content-free invocation traces and deterministic output guardrails."""

from __future__ import annotations

import time
import uuid


def validate_guardrails(policy):
    if not isinstance(policy, dict) or set(policy) - {"max_output_chars", "forbidden_phrases"}:
        raise ValueError("Guardrails support output character limits and forbidden phrases")
    maximum = policy.get("max_output_chars", 262144)
    phrases = policy.get("forbidden_phrases", [])
    if type(maximum) is not int or not 100 <= maximum <= 262144:
        raise ValueError("Output limit must be between 100 and 262144 characters")
    if (
        not isinstance(phrases, list)
        or len(phrases) > 20
        or any(
            not isinstance(value, str) or not value.strip() or len(value) > 200 for value in phrases
        )
    ):
        raise ValueError("Choose up to twenty nonempty forbidden output phrases")
    return {"max_output_chars": maximum, "forbidden_phrases": phrases}


def check_output(output, policy):
    if not policy:
        return
    if len(output) > policy["max_output_chars"]:
        raise ValueError("Agent output exceeded its configured guardrail limit")
    if any(phrase.casefold() in output.casefold() for phrase in policy["forbidden_phrases"]):
        raise ValueError("Agent output was blocked by its configured phrase guardrail")


class StudioObservability:
    def __init__(self, studio):
        self.studio = studio

    async def invoke(self, provider, context, run_id, payload, provider_name, stream=None):
        from nexus.core.studio_workbench import now

        record = {
            "id": uuid.uuid4().hex,
            "run_id": run_id,
            "owner_id": payload.get("requester_nexus_id"),
            "provider": provider_name,
            "model": context.model_override,
            "agent": context.agent_name,
            "prompt_chars": len(context.prompt),
            "created_at": now(),
        }
        started = time.monotonic()
        streaming = callable(stream)
        self.studio.execution.record(
            run_id,
            "invocation",
            status="running",
            source_id=record["id"],
            agent=record["agent"],
            provider=provider_name,
            model=record["model"],
        )
        if streaming:
            self.studio.conversations.begin(run_id, record["id"])
        result = None
        try:
            result = (
                await stream(
                    context,
                    lambda text: self.studio.conversations.delta(run_id, text, record["id"]),
                )
                if streaming
                else await provider.execute_agent(context)
            )
            record.update(
                status="completed" if result.success else "failed", output_chars=len(result.output)
            )
            usage = (result.metadata or {}).get("usage", {})
            if isinstance(usage, dict):
                record["usage"] = {
                    key: value
                    for key, value in usage.items()
                    if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                    and type(value) is int
                    and value >= 0
                }
            return result
        except BaseException:
            record["status"] = "interrupted-or-failed"
            raise
        finally:
            if streaming:
                self.studio.conversations.finish(run_id, record["id"], result)
            record.update(duration_seconds=round(time.monotonic() - started, 3), updated_at=now())
            self.studio._write(self.studio._path("invocation-traces", record["id"]), record)
            self.studio.execution.record(
                run_id,
                "invocation",
                status=record["status"],
                source_id=record["id"],
                agent=record["agent"],
                provider=provider_name,
                model=record["model"],
                duration_seconds=record["duration_seconds"],
            )

    def output(self, text, policy, run_id, payload):
        if not policy:
            return
        try:
            check_output(text, policy)
        except ValueError:
            from nexus.core.studio_workbench import now

            record = {
                "id": uuid.uuid4().hex,
                "run_id": run_id,
                "owner_id": payload.get("requester_nexus_id"),
                "status": "blocked",
                "output_chars": len(text),
                "created_at": now(),
            }
            self.studio._write(self.studio._path("guardrail-events", record["id"]), record)
            raise
