"""Bounded server files through explicit roots; private artifacts bridge to agents."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from nexus.core.studio_artifacts import ARTIFACT_LIMIT


def components(path):
    if not isinstance(path, str) or len(path) > 1024 or "\\" in path:
        raise ValueError("Choose a relative workspace path")
    parts = path.split("/") if path else []
    for part in parts:
        if (
            not part
            or part.startswith(".")
            or len(part.encode()) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in part)
            or ":" in part
            or part.casefold().endswith((".pem", ".key", ".p12", ".pfx"))
            or part.casefold()
            in {
                "credentials",
                "credentials.json",
                "secrets.json",
                "id_rsa",
                "id_ed25519",
                "studio.env",
            }
        ):
            raise ValueError("Hidden, credential and traversal paths are not exposed")
    return parts


class StudioWorkspaceFiles:
    def __init__(self, studio, roots=None):
        self.studio = studio
        self.roots = {
            "files": {"name": "Workspace files", "path": studio.root / "files", "writable": True},
            "agents": {
                "name": "Agent definitions",
                "path": studio.root / "agents",
                "writable": False,
            },
            "workflows": {
                "name": "Workflow definitions",
                "path": studio.root / "workflows",
                "writable": False,
            },
        }
        if roots is not None and (not isinstance(roots, dict) or len(roots) > 12):
            raise ValueError("Configure at most twelve additional workspace file roots")
        for identity, item in (roots or {}).items():
            if (
                not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", identity)
                or identity in self.roots
                or not isinstance(item, dict)
                or set(item) - {"name", "path", "writable"}
                or not isinstance(item.get("path"), str)
                or not Path(item["path"]).is_absolute()
                or type(item.get("writable", False)) is not bool
            ):
                raise ValueError(
                    "File roots require a unique ID, absolute path and optional writable flag"
                )
            name = item.get("name", identity)
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError("File root name must be short text")
            path = Path(item["path"])
            if not path.is_dir() or path.is_symlink():
                raise ValueError("Additional file roots must be existing nonsymlink directories")
            self.roots[identity] = {
                "name": name,
                "path": path.resolve(),
                "writable": item.get("writable", False),
            }
        for item in self.roots.values():
            if item["path"].is_symlink():
                raise ValueError("Workspace file roots cannot be symbolic links")
            path = item["path"].resolve()
            if (
                path == studio.root
                or studio.state.is_relative_to(path)
                or path.is_relative_to(studio.state)
            ):
                raise ValueError(
                    "File roots cannot expose the workspace root or private Studio state"
                )
            item["path"] = path
            if item["writable"] and any(
                path.is_relative_to(studio.root / kind) for kind in ("agents", "workflows")
            ):
                raise ValueError("Definition folders must remain read-only in the file browser")

    def authorize(self, actor):
        if self.studio.team:
            from nexus.core.studio_team import ROLES

            identity = (actor or {}).get("nexus_id")
            self.studio.team.assert_execution(identity)
            if ROLES.get(self.studio.team.bindings.get(identity), -1) < ROLES["builder"]:
                raise PermissionError("Server workspace files require the builder role")

    def root(self, identity, actor, write=False):
        self.authorize(actor)
        if identity not in self.roots:
            raise ValueError("Choose a configured workspace file root")
        root = self.roots[identity]
        if write and not root["writable"]:
            raise PermissionError(
                "Use the validated agent/workflow editor or choose a writable file root"
            )
        return root

    @contextmanager
    def directory(self, root, path="", *, create=False):
        """Walk through directory descriptors; no symlink can swap a path below the root.

        The root itself opens by its resolved administrator path: its ancestors may
        grant search but not read permission (for example /srv/nexus at 0711).
        """
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            try:
                fd = os.open(root["path"], flags)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(root["path"], mode=0o700)
                except FileExistsError:
                    pass
                fd = os.open(root["path"], flags)
        except OSError as exc:
            raise ValueError(
                "Workspace path is unavailable or is not a regular allowed path"
            ) from exc
        try:
            for part in components(path):
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        except OSError as exc:
            raise ValueError(
                "Workspace path is unavailable or is not a regular allowed path"
            ) from exc
        finally:
            os.close(fd)

    def listing(self, identity="files", path="", actor=None):
        root = self.root(identity, actor)
        parts = components(path)
        items, truncated = [], False
        try:
            with self.directory(root, path, create=root.get("writable", False) and not path) as fd, os.scandir(fd) as entries:
                for index, entry in enumerate(entries):
                    if index >= 1000:
                        truncated = True
                        break
                    try:
                        components(entry.name)
                        info = entry.stat(follow_symlinks=False)
                        directory = stat.S_ISDIR(info.st_mode)
                        if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                            continue
                        if identity in {"agents", "workflows"} and (
                            directory or not entry.name.endswith(".yaml")
                        ):
                            continue
                        items.append(
                            {
                                "name": entry.name,
                                "path": "/".join([*parts, entry.name]),
                                "kind": "directory" if directory else "file",
                                "size": info.st_size,
                            }
                        )
                    except (ValueError, OSError):
                        continue
        except ValueError:
            if path or root["path"].exists():
                raise
        items.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
        return {
            "root": identity,
            "path": path,
            "writable": root["writable"],
            "items": items[:200],
            "truncated": truncated or len(items) > 200,
            "roots": [
                {"id": key, "name": value["name"], "writable": value["writable"]}
                for key, value in self.roots.items()
            ],
        }

    def walk(self, identity, path="", actor=None, *, skip=None, max_files=1000, max_dirs=5000):
        """List allowed regular files below a folder, breadth first and bounded."""
        root = self.root(identity, actor)
        if identity in {"agents", "workflows"}:
            raise ValueError("Use the agent and workflow editors for definition folders")
        pending, files, visited, truncated = ["/".join(components(path))], [], 0, False
        while pending and not truncated:
            current = pending.pop(0)
            visited += 1
            if visited > max_dirs:
                truncated = True
                break
            with self.directory(root, current) as fd, os.scandir(fd) as entries:
                found = []
                for entry in entries:
                    try:
                        components(entry.name)
                        info = entry.stat(follow_symlinks=False)
                    except (ValueError, OSError):
                        continue
                    relative = f"{current}/{entry.name}" if current else entry.name
                    directory = stat.S_ISDIR(info.st_mode)
                    if skip and skip(relative, entry.name, directory):
                        continue
                    if directory:
                        found.append((0, relative, 0))
                    elif (
                        stat.S_ISREG(info.st_mode)
                        and info.st_nlink == 1
                        and info.st_size <= ARTIFACT_LIMIT
                    ):
                        found.append((1, relative, info.st_size))
            for kind, relative, size in sorted(found):
                if kind == 0:
                    pending.append(relative)
                elif len(files) >= max_files:
                    truncated = True
                    break
                else:
                    files.append({"path": relative, "size": size})
        return {"files": files, "truncated": truncated}

    def read(self, identity, path, actor=None):
        root = self.root(identity, actor)
        parts = components(path)
        if (
            not parts
            or identity in {"agents", "workflows"}
            and (len(parts) != 1 or not path.endswith(".yaml"))
        ):
            raise ValueError("Choose an exposed file")
        with self.directory(root, "/".join(parts[:-1])) as fd:
            try:
                opened = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(opened, "rb") as file:
                    before = os.fstat(file.fileno())
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_nlink != 1
                        or before.st_size > ARTIFACT_LIMIT
                    ):
                        raise ValueError("Choose a regular single-link file up to 16 MiB")
                    content = file.read(ARTIFACT_LIMIT + 1)
                    after = os.fstat(file.fileno())
                    if len(content) > ARTIFACT_LIMIT or (
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError("File changed while reading; refresh its preview")
            except OSError as exc:
                raise ValueError("File is unavailable or not an allowed regular file") from exc
        return content, hashlib.sha256(content).hexdigest()

    def preview(self, identity, path, actor=None):
        content, revision = self.read(identity, path, actor)
        try:
            text = content[:16384].decode("utf-8") if b"\x00" not in content[:16384] else None
        except UnicodeDecodeError:
            text = None
        return {
            "root": identity,
            "path": path,
            "revision": revision,
            "size": len(content),
            "text": text,
            "truncated": len(content) > 16384,
            "edit_view": "#" + identity if identity in {"agents", "workflows"} else None,
        }

    def upload(self, data, actor=None):
        identity, path = data.get("root", "files"), data.get("path", "")
        root = self.root(identity, actor, write=True)
        names = components(data.get("filename"))
        if len(names) != 1:
            raise ValueError("Choose one filename")
        encoded = data.get("content_base64")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((ARTIFACT_LIMIT + 2) // 3):
            raise ValueError("Upload at most 16 MiB")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("File content must be base64 encoded") from None
        if len(content) > ARTIFACT_LIMIT:
            raise ValueError("Upload at most 16 MiB")
        with self.directory(root, path, create=True) as fd:
            # Exclusive creation intentionally refuses overwrites, including symlinks.
            output = os.open(
                names[0], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            try:
                with os.fdopen(output, "wb") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
            except BaseException:
                os.unlink(names[0], dir_fd=fd)
                raise
        return {
            "root": identity,
            "path": "/".join([*components(path), names[0]]),
            "revision": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

    def folder(self, data, actor=None):
        root = self.root(data.get("root", "files"), actor, write=True)
        names = components(data.get("name"))
        if len(names) != 1:
            raise ValueError("Choose one folder name")
        with self.directory(root, data.get("path", ""), create=True) as fd:
            os.mkdir(names[0], mode=0o700, dir_fd=fd)
        return {"created": True}

    def snapshot(self, data, actor=None):
        identity, path = data.get("root", "files"), data.get("path")
        content, revision = self.read(identity, path, actor)
        if data.get("revision") != revision:
            raise ValueError("File changed; preview its current revision before saving a copy")
        record = self.studio.catalog.save(
            "artifacts",
            {
                "id": "workspace-" + uuid.uuid4().hex,
                "name": Path(path).name,
                "requester_nexus_id": (actor or {}).get("nexus_id"),
                "data": {
                    "filename": Path(path).name,
                    "content_base64": base64.b64encode(content).decode(),
                },
            },
        )
        return {
            "artifact_id": record["id"],
            "artifact_revision": record["revision"],
            "source_revision": revision,
            "download_url": f"/api/catalog/artifacts/{record['id']}/versions/{record['revision']}",
        }
