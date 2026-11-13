"""Account-scoped terminal aliases and prompt-compatible command abbreviations."""

import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

_ALIAS_FILE_LIMIT = 128 * 1024  # JSON escaping can expand the 16 KiB text limit.


def write_preference(path, record):
    """Atomically replace one private preference key without rewriting others."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(json.dumps(record).encode())
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class TerminalCommands:
    def __init__(self, client, builtins):
        self.client = client
        self.builtins = builtins | {"/quit", "/exit"}
        self.memory = {}
        self.directory = None
        scope = getattr(client, "terminal_scope", None)
        if scope and scope.get("workspace"):
            namespace = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
            root = Path(
                os.environ.get("NEXUS_TERMINAL_STATE_DIR")
                or (
                    Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
                    / "nexus-arc/terminal"
                )
            )
            self.directory = root.expanduser() / namespace / "aliases"

    def aliases(self):
        if self.directory is None:
            return dict(self.memory)
        paths = sorted(self.directory.glob("*.json"))
        if len(paths) > 100:
            raise ValueError("Terminal alias storage exceeds 100 entries")
        result = {}
        for path in paths:
            if path.is_symlink() or path.stat().st_size > _ALIAS_FILE_LIMIT:
                raise ValueError("Invalid terminal alias file")
            with path.open("rb") as file:
                raw = file.read(_ALIAS_FILE_LIMIT + 1)
            if len(raw) > _ALIAS_FILE_LIMIT:
                raise ValueError("Invalid terminal alias file")
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError("Invalid terminal alias record")
            if record.get("format") != "nexus-studio-terminal-alias/v1":
                raise ValueError("Unsupported terminal alias format")
            name, text = record.get("name"), record.get("text")
            self.validate(name, text, allow_reserved=True)
            if path.stem != name:
                raise ValueError("Terminal alias filename does not match its record")
            result[name] = text
        return result

    def validate(self, name, text, *, allow_reserved=False):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", name):
            raise ValueError("Alias names use 1–96 letters, numbers, underscores or hyphens")
        if not allow_reserved and "/" + name in self.builtins:
            raise ValueError("Aliases cannot shadow built-in commands")
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16384:
            raise ValueError("Alias text must be nonempty and at most 16 KiB")

    def prompt_names(self):
        return {
            value
            for item in self.client.request("/api/catalog/prompts")["items"]
            if not item.get("archived")
            for value in (item["id"], item.get("data", {}).get("shortcut"))
            if value
        }

    def edit(self, text):
        lexer = shlex.shlex(text, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        lexer.get_token()
        name = lexer.get_token()
        aliases = self.aliases()
        if not name:
            for key, value in sorted(aliases.items()):
                print(f"/{key}: {value}")
            print(f"{len(aliases)} terminal aliases.")
            return
        name = name.lstrip("/")
        expansion = lexer.instream.read().strip()
        if not expansion:
            if name not in aliases:
                raise ValueError("No such terminal alias")
            if self.directory is None:
                del self.memory[name]
            else:
                (self.directory / (name + ".json")).unlink()
            print(f"Removed alias /{name}")
            return
        try:
            tokens = shlex.split(expansion)
        except ValueError:
            tokens = []
        if len(tokens) == 1 and expansion[0] in "\"'":
            expansion = tokens[0]
        self.validate(name, expansion)
        if name in self.prompt_names():
            raise ValueError("Aliases cannot shadow saved prompt IDs or shortcuts")
        if name not in aliases and len(aliases) >= 100:
            raise ValueError("Define at most 100 terminal aliases")
        if self.directory is None:
            self.memory[name] = expansion
        else:
            write_preference(
                self.directory / (name + ".json"),
                {
                    "format": "nexus-studio-terminal-alias/v1",
                    "name": name,
                    "text": expansion,
                },
            )
        print(f"Saved alias /{name}")

    def resolve(self, text, *, expand_alias=True):
        parts = text.split(maxsplit=1)
        if not parts or not parts[0].startswith("/"):
            return text
        head = parts[0]
        if head in self.builtins:
            return text
        aliases = self.aliases()
        matches = sorted(command for command in self.builtins if command.startswith(head))
        alias = aliases.get(head[1:])
        if not matches and alias is None:
            return text
        # Preserve existing exact prompt tokens even when a new command or
        # alias abbreviation happens to share their spelling. Fail closed if
        # access cannot be checked rather than launching a different action.
        if head[1:] in self.prompt_names():
            return text
        if alias is not None:
            if not expand_alias:
                raise ValueError("Alias chains are not supported; use a command or message")
            suffix = text[len(head) :]
            return self.resolve(alias + suffix, expand_alias=False)
        if len(matches) != 1:
            raise ValueError("Ambiguous command: " + ", ".join(matches))
        return matches[0] + text[len(head) :]


def clear_terminal():
    """Clear only an actual terminal; never execute a shell or erase history."""
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="", flush=True)
    else:
        print("Clear screen requires a terminal; conversation history is unchanged.")
