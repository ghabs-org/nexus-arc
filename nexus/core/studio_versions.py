"""Immutable definition versions and reviewed activation/rollback for new runs."""

from __future__ import annotations

import hashlib
import uuid

import yaml


class StudioVersions:
    def __init__(self, studio):
        self.studio = studio

    def remember(self, kind, item):
        if item.get("error"):
            return
        identity, revision = item["id"], item["revision"]
        path = self.studio._path(f"definition-versions/{kind}/{identity}", revision)
        if not path.exists():
            from nexus.core.studio import _now

            self.studio._write(
                path,
                {
                    "id": revision,
                    "definition_id": identity,
                    "kind": kind,
                    "document": item["document"],
                    "created_at": _now(),
                },
            )

    def list(self, kind, identity):
        if kind not in {"agents", "workflows"}:
            raise ValueError("Choose agents or workflows")
        current = next(
            (item for item in self.studio.definitions(kind) if item["id"] == identity), None
        )
        if not current:
            raise FileNotFoundError("Definition not found")
        with self.studio.lock:
            self.remember(kind, current)
            return {
                "current_revision": current["revision"],
                "items": sorted(
                    self.studio.records(f"definition-versions/{kind}/{identity}", limit=None),
                    key=lambda item: item["created_at"],
                    reverse=True,
                ),
            }

    def activate(self, kind, identity, payload, actor):
        from nexus.core.studio import _identity
        from nexus.core.yaml_loader import YamlWorkflowLoader

        identity = _identity(identity)
        version = _identity(payload.get("version"))
        with self.studio.lock:
            versions = self.list(kind, identity)
            if payload.get("revision") != versions["current_revision"]:
                raise ValueError("Definition changed. Review its current version before activation")
            saved = self.studio._read(
                self.studio._path(f"definition-versions/{kind}/{identity}", version)
            )
            document = saved["document"]
            if kind == "workflows":
                if "graph" in document:
                    self.studio.graph.validate(document["graph"])
                else:
                    YamlWorkflowLoader.load_from_dict(document)
            else:
                spec = document["spec"]
                self.studio._definition_document(
                    kind,
                    {
                        **spec,
                        "id": identity,
                        "name": document["metadata"]["name"],
                        "instructions": spec.get("purpose") or spec.get("instructions"),
                    },
                )
            path = self.studio.root / kind / (identity + ".yaml")
            if not path.resolve().is_relative_to(self.studio.root):
                raise ValueError("Definition must stay inside the workspace")
            temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
            try:
                temporary.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            result = {
                "id": identity,
                "document": document,
                "revision": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            self.remember(kind, result)
            from nexus.core.studio import _now

            self.studio._write(
                self.studio._path("definition-activations", uuid.uuid4().hex),
                {
                    "kind": kind,
                    "definition_id": identity,
                    "version": version,
                    "owner_id": actor["nexus_id"] if actor else None,
                    "updated_at": _now(),
                },
            )
            return result

    def snapshots(self):
        return {
            item["id"]: item["document"]["spec"]
            for item in self.studio.definitions("agents")
            if "document" in item
        }
