"""Commit workspace changes to git so any applied change can be reverted with git.

Hosted (team-mode) workspaces get a repository on first use. Desktop workspaces
are versioned only when their own repository opts in with
`git config nexus.studioWorkspace true`, so a developer checkout never receives
unexpected commits.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path

MARKER = "nexus.studioWorkspace"
IGNORED = ".nexus/\n.nexus-*\n.*.tmp\n"
COMMITTER = ["-c", "user.name=Nexus Studio", "-c", "user.email=studio@nexus.local"]


class StudioWorkspaceGit:
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.Lock()  # one git process at a time; index.lock is per repository

    def run(self, *args):
        return subprocess.run(
            ["git", *COMMITTER, *args],
            cwd=self.studio.root,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout.strip()

    def ready(self):
        """Return None when commits can be made, else the reason they cannot."""
        if not shutil.which("git"):
            return "git is not installed"
        root = self.studio.root
        try:
            top = Path(self.run("rev-parse", "--show-toplevel")).resolve()
        except subprocess.CalledProcessError:
            top = None
        if top is not None and top != root.resolve():
            return "the workspace is inside another git repository"
        if top is not None:
            try:
                marked = self.run("config", "--get", MARKER) == "true"
            except subprocess.CalledProcessError:
                marked = False
            return None if marked else f"the workspace repository has not opted in ({MARKER})"
        if not self.studio.team:
            return "desktop workspaces are versioned only after opting in"
        self.run("init", "-q", "-b", "main")
        self.run("config", MARKER, "true")
        ignore = root / ".gitignore"
        if not ignore.exists():
            ignore.write_text(IGNORED, encoding="utf-8")
        self.run(
            "add",
            "-A",
            "--",
            ".gitignore",
            *[k for k in ("agents", "workflows", "files") if (root / k).exists()],
        )
        self.run("commit", "-q", "--allow-empty", "-m", "Nexus Studio: initial workspace snapshot")
        return None

    def commit(self, paths, message, author=None):
        """Commit only these workspace paths; never fails the change that already happened."""
        root = self.studio.root.resolve()
        relative = []
        for path in paths:
            path = Path(path).resolve() if Path(path).is_absolute() else root / path
            if not path.is_relative_to(root):
                return {
                    "versioned": False,
                    "reason": "the file is outside the workspace repository",
                }
            relative.append(str(path.relative_to(root)))
        with self.lock:
            return self._commit(relative, message, author)

    def _commit(self, relative, message, author):
        try:
            reason = self.ready()
            if reason:
                return {"versioned": False, "reason": reason}
            self.run("add", "-A", "--", *relative)
            if not self.run("status", "--porcelain", "--", *relative):
                return {
                    "versioned": True,
                    "commit": self.run("rev-parse", "HEAD"),
                    "unchanged": True,
                }
            name = str(author or "Nexus Studio")
            email = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "studio"
            self.run(
                "commit",
                "-q",
                "--only",
                "-m",
                message,
                f"--author={name} <{email}@nexus.local>",
                "--",
                *relative,
            )
            return {"versioned": True, "commit": self.run("rev-parse", "HEAD")}
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            return {"versioned": False, "reason": "git failed: " + detail.strip()[:300]}
