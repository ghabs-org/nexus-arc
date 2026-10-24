"""Knowledge projects linked to Workspace files folders; sync pins changed files as copies."""

import base64
import fnmatch
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath

from nexus.core.studio_knowledge import IndexInterruptedError
from nexus.core.studio_workspace_files import components

MAX_FOLDERS = 4
MAX_PATTERNS = 20
MAX_SYNC_FILES = 1000
MAX_SYNC_BYTES = 64 * 1024 * 1024
MAX_REPORTED_SKIPS = 50
DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})
TEXT_SUFFIXES = frozenset(
    (
        ".md .mdx .markdown .txt .rst .adoc .org .csv .tsv .json .jsonc .yaml .yml .toml "
        ".ini .cfg .conf .xml .py .pyi .ipynb .js .mjs .cjs .ts .tsx .jsx .vue .svelte .html "
        ".htm .css .scss .less .go .rs .java .kt .kts .scala .rb .php .c .h .cc .cpp .hpp "
        ".cs .swift .m .sh .bash .zsh .fish .ps1 .sql .graphql .proto .tf .hcl .lua .r .dart "
        ".ex .exs .erl .hs .clj .el .vim .service .tex .bib"
    ).split()
)
TEXT_NAMES = frozenset(
    {"Dockerfile", "Makefile", "Justfile", "Procfile", "README", "LICENSE", "CHANGELOG"}
)
# Dependency, build and cache output; hidden folders are never exposed at all.
SKIP_DIRECTORIES = frozenset(
    (
        "node_modules bower_components venv __pycache__ site-packages dist build target "
        "coverage htmlcov vendor"
    ).split()
)
SKIP_FILES = ("*.lock", "package-lock.json", "*.min.js", "*.min.css", "*.map", "*.pyc")
ROOT_ID = re.compile(r"[a-z][a-z0-9-]{0,47}")


def folder_id(root, path):
    return hashlib.sha256(f"{root}:{path}".encode()).hexdigest()[:16]


def _patterns(value, label):
    if not isinstance(value, list) or len(value) > MAX_PATTERNS:
        raise ValueError(f"{label} must be a list of at most {MAX_PATTERNS} patterns")
    for pattern in value:
        if (
            not isinstance(pattern, str)
            or not pattern.strip()
            or len(pattern) > 200
            or any(ord(c) < 32 for c in pattern)
        ):
            raise ValueError(f"{label} must be short text patterns such as *.md or docs/*")
    return [pattern.strip() for pattern in value]


def validate_folders(folders):
    """Validate stored links structurally; access is checked when linking and syncing."""
    if not isinstance(folders, list) or len(folders) > MAX_FOLDERS:
        raise ValueError(f"Link at most {MAX_FOLDERS} folders to a knowledge project")
    result = []
    for folder in folders:
        if not isinstance(folder, dict) or set(folder) - {
            "id",
            "root",
            "path",
            "include",
            "exclude",
        }:
            raise ValueError("Linked folders use root, path, include and exclude")
        root, path = folder.get("root"), folder.get("path", "")
        if not isinstance(root, str) or not ROOT_ID.fullmatch(root):
            raise ValueError("Choose a configured workspace file root")
        if root in {"agents", "workflows"}:
            raise ValueError("Definition folders cannot be linked to knowledge projects")
        path = "/".join(components(path))
        identity = folder_id(root, path)
        if folder.get("id", identity) != identity:
            raise ValueError("Linked folder ID does not match its root and path")
        for other in result:
            if other["root"] == root and (
                not path
                or not other["path"]
                or f"{path}/".startswith(f"{other['path']}/")
                or f"{other['path']}/".startswith(f"{path}/")
            ):
                raise ValueError("Linked folders cannot repeat or contain each other")
        result.append(
            {
                "id": identity,
                "root": root,
                "path": path,
                "include": _patterns(folder.get("include", []), "Include"),
                "exclude": _patterns(folder.get("exclude", []), "Exclude"),
            }
        )
    return result


def selected(folder, relative, name, directory):
    """Apply default skips, then the folder's exclude and include patterns."""
    matches = lambda patterns, value: any(  # noqa: E731
        fnmatch.fnmatchcase(value, pattern) for pattern in patterns
    )
    if directory:
        return not (
            name in SKIP_DIRECTORIES
            or name.endswith(".egg-info")
            or matches(folder["exclude"], relative)
            or matches(folder["exclude"], relative + "/")
        )
    if matches(SKIP_FILES, name) or matches(folder["exclude"], relative):
        return False
    if folder["include"]:
        return matches(folder["include"], relative) or matches(folder["include"], name)
    suffix = PurePosixPath(name).suffix.lower()
    return suffix in TEXT_SUFFIXES or suffix in DOCUMENT_SUFFIXES or name in TEXT_NAMES


class StudioKnowledgeFolders:
    def __init__(self, knowledge):
        self.knowledge = knowledge
        self.studio = knowledge.studio

    def _state_path(self, project_id):
        return self.studio._path("knowledge-folder-sync", project_id)

    def state(self, project_id):
        path = self._state_path(project_id)
        if path.exists():
            return self.studio._read(path)
        return {"id": project_id, "files": {}, "run_id": None, "last_sync": None}

    def status(self, project_id, actor):
        project = self.knowledge.get(project_id, actor, True)
        state = self.state(project_id)
        counts = {}
        for key, entry in state["files"].items():
            if not entry.get("removed"):
                identity = key.split(":", 1)[0]
                counts[identity] = counts.get(identity, 0) + 1
        running = None
        if state.get("run_id"):
            try:
                run = self.studio.run(state["run_id"])
                running = run["id"] if run["status"] in {"queued", "running"} else None
            except FileNotFoundError:
                pass
        return {
            "folders": [
                {**folder, "files": counts.get(folder["id"], 0)}
                for folder in project.get("folders", [])
            ],
            "running_run_id": running,
            "last_sync": state.get("last_sync"),
            "limits": {"folders": MAX_FOLDERS, "files": MAX_SYNC_FILES, "bytes": MAX_SYNC_BYTES},
        }

    def link(self, project_id, data, actor):
        root_id, path = data.get("root"), data.get("path", "")
        if not isinstance(root_id, str):
            raise ValueError("Choose a configured workspace file root")
        root = self.studio.workspace_files.root(root_id, actor)
        folder = validate_folders(
            [
                {
                    "root": root_id,
                    "path": path,
                    "include": data.get("include", []),
                    "exclude": data.get("exclude", []),
                }
            ]
        )[0]
        with self.studio.workspace_files.directory(root, folder["path"]):
            pass
        with self.studio.lock:
            project = self.knowledge.get(project_id, actor)
            if any(item["id"] == folder["id"] for item in project.get("folders", [])):
                raise ValueError("This folder is already linked to the project")
            return self.knowledge.save(
                {**project, "folders": [*project.get("folders", []), folder]}, actor
            )

    def unlink(self, project_id, identity, actor):
        """Stop syncing a folder; its copies leave search but stay for old citations."""
        self.studio.workspace_files.authorize(actor)
        with self.studio.lock:
            project = self.knowledge.get(project_id, actor)
            folders = [item for item in project.get("folders", []) if item["id"] != identity]
            if len(folders) == len(project.get("folders", [])):
                raise FileNotFoundError("Linked folder not found")
            saved = self.knowledge.save({**project, "folders": folders}, actor)
            state = self.state(project_id)
            dropped = {
                key: entry
                for key, entry in state["files"].items()
                if key.split(":", 1)[0] == identity
            }
            self._mark(project_id, {entry["artifact_id"]: True for entry in dropped.values()})
            state["files"] = {k: v for k, v in state["files"].items() if k not in dropped}
            self.studio._write(self._state_path(project_id), state)
            return saved

    def sync(self, project_id, actor):
        self.studio.workspace_files.authorize(actor)
        project = self.knowledge.get(project_id, actor)
        if not project.get("folders"):
            raise ValueError("Link a folder before syncing")
        with self.studio.lock:
            state = self.state(project_id)
            if state.get("run_id"):
                try:
                    if self.studio.run(state["run_id"])["status"] in {"queued", "running"}:
                        raise ValueError("A folder sync is already running for this project")
                except FileNotFoundError:
                    pass
            run = self.studio.start_run(
                {
                    "kind": "knowledge-sync",
                    "task": "Sync linked folders for " + project["name"],
                    "project_id": project_id,
                    "requester_nexus_id": actor["nexus_id"] if actor else None,
                }
            )
            state["run_id"] = run["id"]
            self.studio._write(self._state_path(project_id), state)
        return run

    def _actor(self, payload):
        requester = payload.get("requester_nexus_id")
        if self.studio.team:
            return {"nexus_id": requester, "role": self.studio.team.bindings.get(requester)}
        return {"nexus_id": requester, "role": "operator"} if requester else None

    def _check(self, run_id):
        run = self.studio.run(run_id)
        if run.get("cancel_requested") or run["status"] != "running" or self.studio.closing:
            raise IndexInterruptedError("Folder sync stopped; run Sync now again to continue")

    def _mark(self, project_id, removed):
        """Set or clear source_removed on every pinned revision of the given artifacts."""
        if not removed:
            return
        with self.studio.lock:
            for record in self.studio.records("knowledge-documents", limit=None):
                if record["project_id"] == project_id and record["artifact_id"] in removed:
                    flag = removed[record["artifact_id"]]
                    if bool(record.get("source_removed")) != flag:
                        record["source_removed"] = flag
                        self.studio._write(
                            self.studio._path("knowledge-documents", record["id"]), record
                        )

    def _snapshot(self, artifact_id, path, content, project, actor):
        catalog = self.studio.catalog
        revision = 0
        if catalog._path("artifacts", artifact_id).exists():
            current = catalog.get("artifacts", artifact_id, archived=True)
            self.studio.collaboration.owned(current, actor)
            revision = current["revision"]
        return catalog.save(
            "artifacts",
            {
                "id": artifact_id,
                "revision": revision,
                "name": path[-200:],
                "description": "Copy synced from a folder linked to a knowledge project",
                "requester_nexus_id": None
                if project["owner_id"] == "desktop"
                else project["owner_id"],
                "data": {
                    "filename": PurePosixPath(path).name,
                    "content_base64": base64.b64encode(content).decode(),
                },
            },
        )

    def execute(self, run_id, payload):
        project_id = payload["project_id"]
        actor = self._actor(payload)
        files = self.studio.workspace_files
        files.authorize(actor)
        project = self.knowledge.get(project_id, actor)
        state = self.state(project_id)
        summary = {
            "added": 0,
            "updated": 0,
            "unchanged": 0,
            "restored": 0,
            "removed": 0,
            "failed": 0,
            "bytes": 0,
            "truncated": False,
            "skipped": [],
        }
        seen, complete, completed = set(), set(), False

        def skip(reason, path):
            if len(summary["skipped"]) < MAX_REPORTED_SKIPS:
                summary["skipped"].append({"path": path, "reason": reason})

        try:
            for folder in project.get("folders", []):
                prefix = folder["path"]
                listing = files.walk(
                    folder["root"],
                    prefix,
                    actor,
                    skip=lambda relative, name, directory, folder=folder, prefix=prefix: (
                        not selected(
                            folder,
                            relative[len(prefix) + 1 :] if prefix else relative,
                            name,
                            directory,
                        )
                    ),
                    max_files=max(MAX_SYNC_FILES - len(seen), 0),
                )
                summary["truncated"] |= listing["truncated"]
                finished = not listing["truncated"]
                for item in listing["files"]:
                    self._check(run_id)
                    key = f"{folder['id']}:{item['path']}"
                    if summary["bytes"] + item["size"] > MAX_SYNC_BYTES:
                        summary["truncated"], finished = True, False
                        break
                    seen.add(key)
                    try:
                        content, digest = files.read(folder["root"], item["path"], actor)
                    except ValueError as exc:
                        skip(str(exc), item["path"])
                        continue
                    summary["bytes"] += len(content)
                    entry = state["files"].get(key)
                    if entry and entry["sha256"] == digest:
                        if entry.get("removed"):
                            self._mark(project_id, {entry["artifact_id"]: False})
                            entry["removed"] = False
                            summary["restored"] += 1
                        else:
                            summary["unchanged"] += 1
                        continue
                    artifact = self._snapshot(
                        entry["artifact_id"]
                        if entry
                        else "kfolder-"
                        + hashlib.sha256(f"{project_id}:{key}".encode()).hexdigest()[:40],
                        item["path"],
                        content,
                        project,
                        actor,
                    )
                    document = self.knowledge.attach(
                        project_id,
                        {"artifact_id": artifact["id"], "artifact_revision": artifact["revision"]},
                        actor,
                        source={
                            "folder_id": folder["id"],
                            "root": folder["root"],
                            "path": item["path"],
                            "sha256": digest,
                        },
                        queue=False,
                    )
                    try:
                        self.knowledge.index(document["id"])
                        status = "ready"
                    except Exception:  # the document record keeps its own error and retry
                        status = "failed"
                        summary["failed"] += 1
                    if entry and entry.get("removed"):
                        self._mark(project_id, {artifact["id"]: False})
                    summary["updated" if entry else "added"] += 1
                    state["files"][key] = {
                        "artifact_id": artifact["id"],
                        "sha256": digest,
                        "document_id": document["id"],
                        "status": status,
                        "removed": False,
                    }
                if finished:
                    complete.add(folder["id"])
            gone = {
                key: entry
                for key, entry in state["files"].items()
                if key.split(":", 1)[0] in complete and key not in seen and not entry.get("removed")
            }
            self._mark(project_id, {entry["artifact_id"]: True for entry in gone.values()})
            for entry in gone.values():
                entry["removed"] = True
            summary["removed"] = len(gone)
            completed = True
        finally:
            with self.studio.lock:
                # A folder unlinked while this sync ran must not come back.
                linked = {
                    item["id"]
                    for item in self.knowledge.get(project_id, actor, True).get("folders", [])
                }
                state["files"] = {
                    k: v for k, v in state["files"].items() if k.split(":", 1)[0] in linked
                }
                state["last_sync"] = {
                    "run_id": run_id,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "completed": completed,
                    "summary": summary,
                }
                self.studio._write(self._state_path(project_id), state)
        return json.dumps(summary)
