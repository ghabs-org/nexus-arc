"""Private session selection and local hiding; Studio owns history and labels."""

import hashlib
import json

from nexus.core.studio_terminal_commands import write_preference


class TerminalSessionIndex:
    def __init__(self, alias_directory):
        self.root = alias_directory.parent if alias_directory is not None else None
        self.memory_active = None
        self.memory_hidden = set()

    @staticmethod
    def read(path, format_name):
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink():
            raise ValueError("Local session preferences cannot be symlinks")
        with path.open("rb") as file:
            raw = file.read(4097)
        if len(raw) > 4096:
            raise ValueError("Local session preference exceeds its size limit")
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get("format") != format_name:
            raise ValueError("Invalid or unsupported local session preference")
        return record

    def active(self):
        record = (
            self.read(self.root / "active.json", "nexus-studio-terminal-selection/v1")
            if self.root
            else self.memory_active
        )
        if record is None:
            return None
        for key in ("session_id", "run_id"):
            value = record.get(key)
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 96):
                raise ValueError("Invalid local session selection")
        title = record.get("pending_title")
        if title is not None and (not isinstance(title, str) or not 1 <= len(title) <= 200):
            raise ValueError("Invalid pending conversation title")
        return record

    def select(self, session_id, *, run_id=None, pending_title=None):
        record = {
            "format": "nexus-studio-terminal-selection/v1",
            "session_id": session_id,
            "run_id": run_id,
            "pending_title": pending_title,
        }
        if self.root:
            write_preference(self.root / "active.json", record)
        else:
            self.memory_active = record

    def hidden_path(self, identity):
        key = hashlib.sha256(identity.encode()).hexdigest()
        return self.root / "hidden-sessions" / (key + ".json")

    def hidden(self, identity):
        if self.root is None:
            return identity in self.memory_hidden
        record = self.read(self.hidden_path(identity), "nexus-studio-terminal-hidden/v1")
        if record is not None and record.get("id") != identity:
            raise ValueError("Local hidden-session record does not match")
        return record is not None

    def hide(self, identity):
        if self.root:
            write_preference(
                self.hidden_path(identity),
                {"format": "nexus-studio-terminal-hidden/v1", "id": identity},
            )
        else:
            self.memory_hidden.add(identity)

    def unhide(self, identity):
        if self.root:
            self.hidden_path(identity).unlink(missing_ok=True)
        else:
            self.memory_hidden.discard(identity)
