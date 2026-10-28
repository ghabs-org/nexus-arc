"""Assistant file changes as proposals; nothing is written until the requester applies one."""

from __future__ import annotations

import uuid

from nexus.core.studio_workspace_files import components

TEXT_LIMIT = 1024 * 1024
BEFORE_LIMIT = 16384


class StudioWorkspaceProposals:
    def __init__(self, studio):
        self.studio = studio

    @staticmethod
    def owner(actor):
        return (actor or {}).get("nexus_id") or "desktop"

    def propose(self, root, path, content, base_revision=None, actor=None, run_id=None):
        studio = self.studio
        files = studio.workspace_files
        files.root(root, actor, write=True)
        parts = components(path)
        if not parts:
            raise ValueError("Choose a file path")
        if parts[:-1]:
            try:
                files.listing(root, "/".join(parts[:-1]), actor)
            except ValueError:
                raise ValueError(
                    f"Folder {'/'.join(parts[:-1])} does not exist; ask the user to create it first"
                ) from None
        if not isinstance(content, str) or len(content.encode()) > TEXT_LIMIT:
            raise ValueError("Propose text content up to 1 MiB")
        if base_revision is not None and not isinstance(base_revision, str):
            raise ValueError("base_revision must be the revision returned by workspace_read")
        try:
            current = files.preview(root, path, actor)
        except ValueError:
            current = None
        if current and current["revision"] != base_revision:
            raise ValueError(
                "File exists or changed; read it with workspace_read and pass its revision as base_revision"
            )
        if not current and base_revision is not None:
            raise ValueError("File does not exist; omit base_revision to create it")
        from nexus.core.studio import _now

        record = {
            "id": uuid.uuid4().hex,
            "run_id": run_id,
            "owner_id": self.owner(actor),
            "root": root,
            "path": "/".join(components(path)),
            "content": content,
            "base_revision": base_revision,
            "before": (current or {}).get("text"),
            "before_truncated": bool((current or {}).get("truncated")),
            "status": "pending",
            "created_at": _now(),
        }
        studio._write(studio._path("workspace-proposals", record["id"]), record)
        return {
            "proposal_id": record["id"],
            "status": "pending",
            "notice": "Shown to the user with Apply and Reject; the file is unchanged until they apply it.",
        }

    def list(self, run_id, actor=None):
        owner = self.owner(actor)
        return {
            "items": [
                row
                for row in self.studio.records("workspace-proposals", limit=None)
                if row.get("run_id") == run_id and row.get("owner_id") == owner
            ]
        }

    def decide(self, identity, action, actor=None):
        studio = self.studio
        if action not in {"apply", "reject"}:
            raise ValueError("Choose apply or reject")
        from nexus.core.studio import _identity, _now

        path = studio._path("workspace-proposals", _identity(identity))
        with studio.lock:
            if not path.exists():
                raise FileNotFoundError("Proposal not found")
            record = studio._read(path)
            if record["owner_id"] != self.owner(actor):
                raise PermissionError("Only the person who asked for this change can decide it")
            if record["status"] != "pending":
                raise ValueError(f"Proposal already {record['status']}")
            if action == "apply":
                written = studio.workspace_files.write(
                    record["root"],
                    record["path"],
                    record["content"].encode(),
                    record["base_revision"],
                    actor,
                )
                record["revision"] = written["revision"]
                target = studio.workspace_files.roots[record["root"]]["path"] / record["path"]
                record["git"] = studio.workspace_git.commit(
                    [target], f"Apply assistant change to {record['path']}", self.owner(actor)
                )
            record.update(status="applied" if action == "apply" else "rejected", decided_at=_now())
            studio._write(path, record)
            return record
