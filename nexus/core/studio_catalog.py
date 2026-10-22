"""Versioned local resources shared by Studio and other Nexus consumers."""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from datetime import UTC, datetime

KINDS = (
    "prompts",
    "skills",
    "artifacts",
    "toolsets",
    "evaluations",
    "schedules",
    "connectors",
    "entrypoints",
    "models",
    "externalagents",
    "decisionmodels",
)


def _text(value, label, maximum=131072):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{label} must be nonempty text, at most {maximum} bytes")
    return value


class StudioCatalog:
    """Persist resources with optimistic revisions and reversible archival.

    Content is data: storing a skill/toolset never imports code or installs tools.
    Artifact attachments are explicit UTF-8 context supplied by the user.
    """

    def __init__(self, studio):
        self.studio = studio

    def _path(self, kind, identity):
        if kind not in KINDS:
            raise ValueError("Unknown resource collection")
        return self.studio._path(f"catalog/{kind}", identity)

    def get(self, kind, identity, *, archived=False):
        record = self.studio._read(self._path(kind, identity))
        if record["archived"] and not archived:
            raise ValueError("Resource is archived; restore it before use")
        return record

    def list(self, kind, *, archived=False):
        self._path(kind, "index")
        records = self.studio.records(f"catalog/{kind}", limit=None)
        result = []
        for record in records:
            if not archived and record["archived"]:
                continue
            item = {**record, "data": dict(record["data"])}
            if kind == "artifacts":
                item["data"].pop("content_base64", None)
            result.append(item)
        return result

    def validate(self, kind, data, targets=None):
        if not isinstance(data, dict):
            raise ValueError("Resource data must be an object")
        if kind == "prompts":
            from nexus.core.studio_prompts import validate_prompt

            return validate_prompt(data)
        if kind == "skills":
            skill = {"content": _text(data.get("content"), "Content")}
            source = data.get("source")
            if source is not None:
                # Where an imported skill came from, for display and re-import.
                fields = {"type", "repository", "ref", "commit", "path"}
                if (
                    not isinstance(source, dict)
                    or set(source) - fields
                    or any(not isinstance(v, str) or len(v) > 500 for v in source.values())
                ):
                    raise ValueError("Skill source must name its repository, ref, commit and path")
                skill["source"] = dict(source)
            return skill
        if kind == "toolsets":
            from nexus.tools import tool_registry

            tools = data.get("tools")
            if not isinstance(tools, list) or not tools or len(tools) > 100:
                raise ValueError("Choose between 1 and 100 registered tools")
            if any(not isinstance(name, str) or not tool_registry.get(name) for name in tools):
                raise ValueError("Toolsets can only reference registered tools")
            return {"tools": list(dict.fromkeys(tools))}
        if kind == "artifacts":
            filename = _text(data.get("filename"), "Filename", 200)
            if "/" in filename or "\\" in filename or any(ord(c) < 32 for c in filename):
                raise ValueError(
                    "Use a filename without directory separators or control characters"
                )
            encoded = data.get("content_base64")
            if not isinstance(encoded, str):
                raise ValueError("Artifact content must be base64 encoded")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Invalid artifact encoding") from exc
            from nexus.core.studio_artifacts import ARTIFACT_LIMIT

            if len(content) > ARTIFACT_LIMIT:
                raise ValueError("Artifacts must be at most 16 MiB")
            return {
                "filename": filename,
                "content_base64": encoded,
                "size": len(content),
                "content_type": "application/octet-stream",
            }
        return self.studio.operations.validate(kind, data, targets)

    def save(self, kind, payload):
        name = _text(payload.get("name"), "Name", 200).strip()
        identity = (
            payload.get("id")
            or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            or uuid.uuid4().hex
        )
        path = self._path(kind, identity)
        description = payload.get("description", "")
        if not isinstance(description, str) or len(description) > 2000:
            raise ValueError("Description must be text, at most 2000 characters")
        data = self.validate(kind, payload.get("data"))
        with self.studio.lock:
            if kind == "prompts":
                aliases = {identity, data.get("shortcut")} - {None, ""}
                for prompt in self.list("prompts"):
                    if prompt["id"] != identity and aliases & {
                        prompt["id"],
                        prompt["data"].get("shortcut"),
                    }:
                        raise ValueError(
                            "Prompt shortcut conflicts with an existing prompt ID or shortcut"
                        )
            previous = self.get(kind, identity, archived=True) if path.exists() else None
            revision = previous["revision"] if previous else 0
            supplied = payload.get("revision", 0)
            if type(supplied) is not int or supplied != revision:
                raise ValueError("Resource changed. Reopen its latest revision before saving.")
            now = datetime.now(UTC).isoformat()
            record = {
                "id": identity,
                "kind": kind,
                "owner_id": payload.get("requester_nexus_id")
                or (previous.get("owner_id") if previous else None),
                "name": name,
                "description": description,
                "data": data,
                "revision": revision + 1,
                "archived": False,
                "created_at": previous["created_at"] if previous else now,
                "updated_at": now,
            }
            if kind == "artifacts":
                record["data"] = self.studio.artifacts.put(record)
            if previous:
                self.studio._write(
                    self.studio._path(f"catalog-history/{kind}/{identity}", str(revision)), previous
                )
            self.studio._write(path, record)
            if kind == "connectors":
                self.studio.operations.reconcile_connectors()
            return record

    def archive(self, kind, identity, payload):
        with self.studio.lock:
            record = self.get(kind, identity, archived=True)
            if (
                type(payload.get("revision")) is not int
                or payload["revision"] != record["revision"]
            ):
                raise ValueError("Resource changed. Reopen its latest revision before archiving.")
            archived = payload.get("archived", True)
            if type(archived) is not bool:
                raise ValueError("Archived must be true or false")
            self.studio._write(
                self.studio._path(f"catalog-history/{kind}/{identity}", str(record["revision"])),
                record,
            )
            record.update(
                archived=archived,
                revision=record["revision"] + 1,
                updated_at=datetime.now(UTC).isoformat(),
            )
            self.studio._write(self._path(kind, identity), record)
            if kind == "connectors":
                self.studio.operations.reconcile_connectors()
            return record

    def history(self, kind, identity):
        self.get(kind, identity, archived=True)
        return self.studio.records(f"catalog-history/{kind}/{identity}", limit=None)

    def artifact(self, identity):
        record = self.get("artifacts", identity)
        return record["data"]["filename"], self.studio.artifacts.get(record)

    def context(self, payload, *, require_active=False, artifacts_as_files=False):
        sections = []
        for key, kind in [
            ("prompt_ids", "prompts"),
            ("skill_ids", "skills"),
            ("artifact_ids", "artifacts"),
        ]:
            identities = payload.get(key, [])
            maximum = 20 if kind == "skills" else 5
            if (
                not isinstance(identities, list)
                or len(identities) > maximum
                or any(not isinstance(i, str) for i in identities)
            ):
                raise ValueError(f"{key} must be a list of up to {maximum} resource IDs")
            for identity in dict.fromkeys(identities):
                if require_active and kind != "artifacts":
                    self.studio.resource_lifecycle.assert_launch(kind + ":" + identity)
                item = self.get(kind, identity)
                if (
                    kind == "artifacts"
                    and self.studio.team
                    and not self.studio.team.visible(
                        item,
                        {
                            "nexus_id": payload.get("requester_nexus_id"),
                            "role": payload.get("_role"),
                        },
                    )
                ):
                    raise PermissionError("Artifact is private to another user")
                if kind == "artifacts":
                    from nexus.core.studio_artifacts import IMAGE_TYPES, content_type

                    ref = next(
                        (
                            ref
                            for ref in payload.get("artifact_refs", [])
                            if ref.get("id") == identity
                        ),
                        {},
                    )
                    item = self.studio.artifacts.revision(identity, ref.get("revision"))
                    if artifacts_as_files:
                        sections.append(
                            f"\nAttached file: {item['data']['filename']} (revision {item['revision']}). "
                            "Original content will be supplied separately to the selected provider."
                        )
                        continue
                    binary = self.studio.artifacts.get(item)
                    mime = content_type(binary)
                    if mime in IMAGE_TYPES or mime == "application/pdf":
                        content = f"Attached {mime}: {item['data']['filename']} (revision {item['revision']}). Its content is supplied separately to compatible providers."
                        sections.append(
                            f"\n--- user-supplied artifact ---\n{content}\n--- end resource ---"
                        )
                        continue
                    try:
                        content = binary.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ValueError(
                            "Only UTF-8 text artifacts can be attached to prompts"
                        ) from exc
                    if "\x00" in content:
                        raise ValueError("Binary artifacts cannot be used as text context")
                else:
                    content = item["data"]["content"]
                sections.append(
                    f"\n--- {kind}: {item['name']} (user-supplied content) ---\n{content}\n--- end resource ---"
                )
        joined = "\n".join(sections)
        if len(joined.encode("utf-8")) > 131072:
            raise ValueError("Combined resource context must be at most 128 KiB")
        return joined

    def expand(self, payload):
        """Resolve known /prompt and @agent tokens once; never execute their content."""
        payload = dict(payload)
        task = payload.get("task") or ""
        if not isinstance(task, str):
            raise ValueError("Task must be text")
        prompts = {r["id"]: r for r in self.list("prompts")}
        prompts.update(
            {r["data"]["shortcut"]: r for r in list(prompts.values()) if r["data"].get("shortcut")}
        )
        agents = {r["id"]: r for r in self.studio.definitions("agents") if r.get("document")}
        references = []

        def prompt(match):
            resource = prompts.get(match[1])
            if not resource:
                return match[0]
            self.studio.resource_lifecycle.assert_launch("prompts:" + resource["id"])
            from nexus.core.studio_prompts import render_prompt

            all_values = payload.get("prompt_values", {})
            if not isinstance(all_values, dict):
                raise ValueError("Prompt values must be an object keyed by prompt ID")
            return render_prompt(resource["data"], all_values.get(resource["id"], {}))

        def agent(match):
            resource = agents.get(match[1])
            if not resource:
                return match[0]
            references.append(resource)
            return resource["document"].get("metadata", {}).get("name", match[1])

        expanded = re.sub(r"(?<!\S)/([A-Za-z0-9][\w-]{0,95})(?=\s|$)", prompt, task)
        expanded = re.sub(r"(?<!\S)@([A-Za-z0-9][\w-]{0,95})(?=\s|$)", agent, expanded)
        identities = payload.get("artifact_ids", [])
        if (
            not isinstance(identities, list)
            or len(identities) > 5
            or any(not isinstance(value, str) for value in identities)
        ):
            raise ValueError("artifact_ids must be a list of up to five resource IDs")
        if identities:
            # Pin immutable versions; workers/retries never reload a newer attachment silently.
            payload["artifact_refs"] = [
                {"id": identity, "revision": self.get("artifacts", identity)["revision"]}
                for identity in payload["artifact_ids"]
            ]
        if "expected_artifact_refs" in payload:
            expected = payload["expected_artifact_refs"]
            if (
                not isinstance(expected, list)
                or len(expected) > 5
                or any(
                    not isinstance(ref, dict)
                    or set(ref) != {"id", "revision"}
                    or not isinstance(ref.get("id"), str)
                    or type(ref.get("revision")) is not int
                    or ref["revision"] < 1
                    for ref in expected
                )
            ):
                raise ValueError(
                    "Expected attachments must name up to five IDs and positive revisions"
                )
            if expected != (payload.get("artifact_refs", []) if identities else []):
                raise ValueError(
                    "Attachment selection or revision changed; select files explicitly before resubmitting"
                )
        # Remote attachments travel as pinned binary parts, not text extraction.
        context = self.context(
            {**payload, "artifact_ids": []} if payload.get("kind") == "external" else payload,
            require_active=True,
            artifacts_as_files=True,
        )
        payload["artifact_context_mode"] = "references"
        for resource in references:
            context += f"\nReferenced agent {resource['id']}:\n" + str(
                resource["document"].get("spec", {}).get("purpose", "")
            )
        if len(context.encode("utf-8")) + len(expanded.encode("utf-8")) > 262144:
            raise ValueError("Expanded request must be at most 256 KiB")
        payload["expanded_task"] = expanded
        payload["reference_context"] = context
        if (
            len({r["id"] for r in references}) == 1
            and not payload.get("agent_id")
            and payload.get("kind", "chat") == "chat"
        ):
            payload["agent_id"] = references[0]["id"]
        return payload

    def capture_run(self, run):
        """Keep completed output available as a reusable text artifact."""
        if not run.get("output"):
            return None
        content = str(run["output"]).encode("utf-8")
        if len(content) > 512 * 1024:
            return None
        return self.save(
            "artifacts",
            {
                "id": f"run-{run['id']}",
                "requester_nexus_id": run.get("requester_nexus_id"),
                "name": run["task"][:160] or "Run result",
                "description": f"Output from {run['kind']} run {run['id']}",
                "data": {
                    "filename": f"{run['id']}.txt",
                    "content_base64": base64.b64encode(content).decode(),
                },
            },
        )
