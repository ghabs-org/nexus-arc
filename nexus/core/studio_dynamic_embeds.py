"""Resolve bounded late-bound response values without executable expressions."""

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime

from nexus.core.studio_connections import execution_identity
from nexus.core.studio_embed_values import (
    LIMIT,
    bounded,
    calculation,
    decode,
    encode,
    format_data,
    number_format,
    select,
    template,
    transform,
)

DIRECTIVE = re.compile(r"«([^«»]*)»")
TOTAL = 262144


class StudioDynamicEmbeds:
    def __init__(self, studio):
        from nexus.tools import tool_registry

        self.studio = studio
        name = (
            "studio:"
            + hashlib.sha256(str(studio.state).encode()).hexdigest()[:12]
            + ":render_embeds"
        )

        async def invoke(text, state=None):
            actor = self.actor()
            return self.render(text, state=state, actor=actor)

        tool_registry.register(
            name,
            invoke,
            category="Data and media",
            description="Render bounded «state:path», «math:expression», «datetime:format» and owned artifact embeds. Arguments: text, optional explicit JSON state. No code, network or recursive evaluation.",
        )
        self.names = {name: invoke}

    def actor(self):
        if not self.studio.team:
            return None
        identity = execution_identity.get()
        self.studio.team.assert_execution(identity)
        return {"nexus_id": identity, "role": self.studio.team.bindings[identity]}

    def close(self):
        from nexus.tools import tool_registry

        for name, function in self.names.items():
            tool_registry.unregister(name, func=function)

    def inventory(self):
        return {
            "items": [
                {
                    "id": name,
                    "configured": True,
                    "billing": "Local processing; no API calls",
                    "setup": "Built in. Explicit state and authorized artifact revisions only.",
                }
                for name in self.names
            ]
        }

    def render(self, text, *, state=None, actor=None, now=None):
        bounded(text, TOTAL)
        state = {} if state is None else state
        if not isinstance(state, dict):
            raise ValueError("Embed state must be an explicit JSON object")
        try:
            state = json.loads(bounded(json.dumps(state, allow_nan=False), 65536))
        except (TypeError, OverflowError, RecursionError):
            raise ValueError("Embed state must be bounded finite JSON") from None
        if self.studio.team:
            if not actor:
                raise PermissionError("Embed execution requires Nexus identity")
            self.studio.team.assert_execution(actor["nexus_id"])
        now = now or datetime.now(UTC)
        parts, statuses, references, cache = [], [], [], {}
        budget = {"count": 0, "bytes": 0, "output": 0}

        def artifact(spec):
            if len(spec) > 512:
                raise ValueError("Artifact selector exceeds limit")
            name, separator, revision = spec.rpartition(":")
            if not separator:
                name, revision = spec, None
            elif not revision.isdigit() or len(revision) > 9 or int(revision) < 1:
                raise ValueError("Artifact revision must be a positive integer")
            revision = int(revision) if revision else None
            key = (name, revision)
            if key in cache:
                return cache[key]
            records = []
            for record in self.studio.catalog.list("artifacts"):
                if name not in (record["id"], record["data"]["filename"]):
                    continue
                try:
                    self.studio.collaboration.owned(record, actor)
                    records.append(record)
                except PermissionError:
                    continue
            if len(records) != 1:
                raise ValueError("Artifact is unavailable or ambiguous")
            try:
                record = self.studio.artifacts.revision(records[0]["id"], revision)
                self.studio.collaboration.owned(record, actor)
            except (FileNotFoundError, PermissionError, ValueError):
                raise ValueError("Artifact is unavailable or ambiguous") from None
            cache[key] = record
            reference = {
                "artifact_id": record["id"],
                "revision": record["revision"],
                "filename": record["data"]["filename"],
            }
            if reference not in references:
                references.append(reference)
            return record

        def content(record):
            if record["data"].get("size", LIMIT + 1) > LIMIT:
                raise ValueError("Artifact exceeds embed size limit")
            budget["bytes"] += record["data"].get("size", LIMIT + 1)
            if budget["bytes"] > 131072:
                raise ValueError("Total artifact read limit exceeded")
            raw = self.studio.artifacts.get(record)
            if len(raw) > LIMIT:
                raise ValueError("Artifact exceeds embed size limit")
            return decode(raw, record["data"]["filename"])

        def attach(record):
            part = {
                "type": "artifact",
                "artifact_id": record["id"],
                "revision": record["revision"],
                "filename": record["data"]["filename"],
            }
            if part not in parts:
                parts.append(part)
            return ""

        def resolve(directive):
            kind, sep, expression = directive.partition(":")
            if not sep:
                raise ValueError("Directive requires a type and expression")
            kind, expression = kind.strip(), expression.strip()
            if kind == "state":
                path, _, spec = expression.partition("|")
                value = select(state, path.strip())
                if spec.strip():
                    if type(value) not in (int, float):
                        raise ValueError("State formatting requires a number")
                    return number_format(value, spec.strip())
                return bounded(value if isinstance(value, str) else encode(value))
            if kind == "math":
                source, _, spec = expression.partition("|")
                value = calculation(source.strip())
                return number_format(value, spec.strip()) if spec.strip() else str(value)
            if kind == "datetime":
                if len(expression) > 128:
                    raise ValueError("Date format exceeds limit")
                if any(
                    code not in "aAbBcdHIjmMpSUwWxXyYZfzGVu%"
                    for code in re.findall(r"%(.)", expression)
                ):
                    raise ValueError("Unsupported date format")
                return bounded(now.strftime(expression or "%Y-%m-%dT%H:%M:%SZ"))
            if kind == "uuid" and not expression:
                return str(uuid.uuid4())
            if kind == "status_update":
                statuses.append(bounded(expression, 512))
                return ""
            if kind == "artifact_meta":
                record = artifact(expression)
                return encode(
                    {
                        "artifact_id": record["id"],
                        "revision": record["revision"],
                        **{
                            k: v
                            for k, v in record["data"].items()
                            if k in {"filename", "size", "content_type", "sha256"}
                        },
                    }
                )
            if kind == "artifact_return":
                return attach(artifact(expression))
            if kind == "artifact_content":
                steps = [step.strip() for step in expression.split(">>>")]
                if len(steps) < 2 or len(steps) > 14 or not steps[-1].startswith("format:"):
                    raise ValueError(
                        "Content chain requires a final format and at most 12 modifiers"
                    )
                record = artifact(steps[0])
                output_format = steps[-1][7:]
                if output_format == "datauri":
                    if len(steps) != 2:
                        raise ValueError("Binary attachments cannot have data modifiers")
                    return attach(record)
                value = content(record)
                for step in steps[1:-1]:
                    if step.startswith("apply_to_template:"):
                        source = content(artifact(step.partition(":")[2]))
                        if not isinstance(source, str):
                            raise ValueError("Template artifact must contain text")
                        value = template(source, value)
                    else:
                        value = transform(value, step)
                    bounded(value if isinstance(value, str) else encode(value))
                return format_data(value, output_format)
            raise ValueError("Unsupported embed type")

        budget["remaining"] = TOTAL - len(text.encode("utf-8"))

        def replace(match):
            budget["count"] += 1
            if budget["count"] > 32:
                value = "[Embed unavailable: directive limit exceeded]"
            else:
                try:
                    value = bounded(resolve(match[1]))
                except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
                    value = "[Embed unavailable: invalid, unauthorized or over-limit directive]"
            growth = len(value.encode("utf-8")) - len(match[0].encode("utf-8"))
            if growth > budget["remaining"]:
                value = "[Embed unavailable: output limit exceeded]"
                growth = len(value.encode("utf-8")) - len(match[0].encode("utf-8"))
                if growth > budget["remaining"]:
                    return match[0]
            budget["remaining"] -= growth
            return value

        result = DIRECTIVE.sub(replace, text)
        bounded(result, TOTAL)
        return {
            "version": 1,
            "text": result,
            "structured_content": parts,
            "artifact_refs": references,
            "status_updates": statuses,
        }

    def complete(self, text, payload, run_id):
        if "«" not in text:
            return text, []
        actor = self.actor()
        public_state = {
            key: payload.get(key) for key in ("task", "session_id", "agent_id", "provider", "model")
        }
        public_state["run_id"] = run_id
        resolved = self.render(text, state=public_state, actor=actor)
        for status in resolved["status_updates"]:
            self.studio.execution.record(run_id, "embed-status", status="completed", message=status)
        self.studio._update_run(
            run_id, embed_version=1, embed_artifact_refs=resolved["artifact_refs"]
        )
        return resolved["text"], resolved["structured_content"]
