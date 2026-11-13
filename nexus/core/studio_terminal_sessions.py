"""Interactive session commands backed by Studio's existing authorized records."""

import json
import shlex
from pathlib import Path
from urllib.parse import quote

from nexus.core.studio_http_client import write_private_file
from nexus.core.studio_terminal_agents import agent_choices, directed_message, display_entrypoints
from nexus.core.studio_terminal_commands import TerminalCommands, clear_terminal
from nexus.core.studio_terminal_files import TerminalFiles
from nexus.core.studio_terminal_index import TerminalSessionIndex
from nexus.core.studio_terminal_retry import TerminalRetry
from nexus.core.studio_terminal_ui import render_markdown


class TerminalSessions:
    """Keep only the current selection locally; Studio owns durable history."""

    commands = {
        "/new",
        "/sessions",
        "/switch",
        "/rename",
        "/history",
        "/export",
        "/help",
        "/upload",
        "/artifacts",
        "/download",
        "/multiline",
        "/agents",
        "/agent",
        "/entrypoints",
        "/alias",
        "/clear",
        "/retry",
        "/delete",
    }

    def __init__(self, client, session_id=None, artifact_ids=None):
        self.client = client
        self.session_id = session_id
        self.files = TerminalFiles(client)
        self.artifact_ids = list(artifact_ids or [])
        self.pending_task = ""
        self.pending_agent_id = None
        self.multiline = False
        self.enhanced_input = False
        self.plain = False
        self.exit_requested = False
        self.shortcuts = TerminalCommands(client, self.commands)
        self.retry = TerminalRetry(client)
        self.pending_retry = None
        self.index = TerminalSessionIndex(self.shortcuts.directory)
        self.pending_title = None

    def restore(self):
        saved = self.index.active()
        if saved is None:
            return
        identity = saved.get("session_id")
        if not identity:
            self.pending_title = saved.get("pending_title")
            if (
                self.pending_title
                and getattr(self.client, "capabilities", {}).get("session_title") is not True
            ):
                raise ValueError(
                    "Saved conversation title requires server support; use --new-session to start fresh"
                )
            if self.pending_title:
                print(f"Pending new conversation · {self.pending_title}")
            return
        if self.index.hidden(identity):
            print("Last conversation was removed from this terminal list; use /new or /switch.")
            return
        if saved.get("run_id"):
            run = self.client.request("/api/runs/" + quote(saved["run_id"], safe=""))
            if run.get("session_id") != identity or run.get("status") not in {
                "completed",
                "failed",
                "cancelled",
            }:
                raise ValueError(
                    f"Saved run {saved['run_id']} needs inspection; use --run-id or --new-session. No task was resubmitted."
                )
        record = self.current_record(identity)
        self.session_id = record["id"]
        print(f"Restored {record['id']} · {record.get('title', '')}")

    def current_record(self, identity):
        record = self.client.request("/api/sessions/" + quote(identity, safe=""))
        if record.get("archived"):
            raise ValueError("Restore this conversation in Studio or use --new-session")
        return record

    def select(self, record):
        self.index.unhide(record["id"])
        self.index.select(record["id"])
        self.session_id, self.pending_title = record["id"], None

    def accepted(self, run):
        self.session_id, self.pending_title = run["session_id"], None
        try:
            self.index.select(run["session_id"], run_id=run["id"])
        except (ValueError, OSError):
            raise ValueError(
                f"Run {run['id']} was accepted but local selection could not be saved. Inspect --run-id {run['id']}; do not resubmit."
            ) from None

    def visible_sessions(self):
        return [
            item
            for item in self.client.request("/api/sessions")["items"]
            if not self.index.hidden(item["id"])
        ]

    def dispatch(self, text):
        """Return false for ordinary messages, including existing prompt shortcuts."""
        try:
            original = text
            text = self.shortcuts.resolve(text)
            head = text.split(maxsplit=1)[0] if text.strip() else ""
            if head in {"/quit", "/exit"}:
                if text.strip() != head:
                    raise ValueError("Usage: /quit or /exit")
                self.exit_requested = True
                return True
            if head not in self.commands:
                if text != original:
                    self.pending_task = text
                    return True
                return False
            if head == "/alias":
                self.shortcuts.edit(text)
                return True
            if head == "/agent":
                identity, message = directed_message(text, self.client)
                self.pending_agent_id, self.pending_task = identity, message
                return True
            command, *arguments = shlex.split(text)
            self.execute(command, arguments)
        except (ValueError, OSError) as error:
            print(f"Command failed: {error}")
        return True

    def current(self):
        if not self.session_id:
            raise ValueError("Send a message or /switch to a saved conversation first")
        return self.current_record(self.session_id)

    def execute(self, command, arguments):
        if command == "/help":
            if arguments:
                raise ValueError("Usage: /help")
            print(
                "/new [title] · /sessions · /switch <id or exact title> · /rename <title>\n"
                "/delete <id or exact title> (local list only; server history remains)\n"
                "/history [count] · /export [md|json|txt] [new file] · /quit · /exit\n"
                "/upload <local file> [message] · /artifacts · /download [id:revision] [new path]\n"
                "/multiline (enhanced terminal: Alt+Enter sends)\n"
                "/agents · /agent <id or quoted name> <message> · /entrypoints\n"
                "/alias [name] [text] · /clear · unambiguous command prefixes\n"
                "/retry (explicitly resend the last completed chat turn)\n"
                "Other slash messages retain Studio prompt-shortcut behavior."
            )
        elif command == "/new":
            title = " ".join(arguments).strip() or None
            if title and (
                len(title) > 200
                or getattr(self.client, "capabilities", {}).get("session_title") is not True
            ):
                raise ValueError(
                    "Named conversations require server title support and a title of at most 200 characters"
                )
            self.index.select(None, pending_title=title)
            self.session_id = None
            self.pending_title = title
            print("New conversation selected; it is saved when you send a message.")
            if title:
                print(f"Pending title · {title}")
        elif command in {"/sessions", "/switch", "/delete"}:
            if command == "/sessions" and arguments:
                raise ValueError("Usage: /sessions")
            if command != "/sessions" and not arguments:
                raise ValueError(f"Usage: {command} <id or exact title>")
            items = self.visible_sessions()
            if command == "/sessions":
                for item in items:
                    marker = "*" if item["id"] == self.session_id else " "
                    print(
                        f"{marker} {item['id']} · {item.get('title', '')} · {item.get('message_count', '?')} saved messages · last active {item.get('updated_at') or item.get('created_at', 'unknown')}"
                    )
                if self.pending_title:
                    print(f"Pending new conversation · {self.pending_title}")
                print(f"{len(items)} visible conversations (server limit: 100).")
            else:
                selected = " ".join(arguments)
                # An explicit ID wins over a title collision. Direct lookup also
                # supports an owned conversation outside the listing's first 100.
                exact = [item for item in items if item["id"] == selected]
                matches = exact or [item for item in items if item.get("title") == selected]
                if len(matches) > 1:
                    raise ValueError("Several conversations share that title; use an ID")
                identity = matches[0]["id"] if matches else selected
                record = self.current_record(identity)
                if command == "/delete":
                    self.index.hide(record["id"])
                    if self.session_id == record["id"]:
                        self.index.select(None)
                        self.session_id, self.pending_title = None, None
                    print(
                        f"Removed {record['id']} from the local terminal list. Server history and artifacts remain."
                    )
                else:
                    self.select(record)
                    print(f"Selected {record['id']} · {record.get('title', '')}")
        elif command == "/rename":
            title = " ".join(arguments).strip()
            if not title or len(title) > 200:
                raise ValueError("Usage: /rename <title of 1–200 characters>")
            record = self.current()
            self.client.request(
                "/api/workbench/memory/" + quote(record["id"], safe="") + "/edit",
                {"title": title},
            )
            print(f"Renamed conversation to {title}")
        elif command == "/history":
            if len(arguments) > 1:
                raise ValueError("Usage: /history [count from 1 to 1000]")
            count = int(arguments[0]) if arguments else 20
            if not 1 <= count <= 1000:
                raise ValueError("History count must be 1–1000")
            for message in self.current().get("messages", [])[-count:]:
                render_markdown(
                    message.get("content", ""),
                    plain=self.plain,
                    label=f"{message.get('role', 'message')}:",
                )
        elif command == "/export":
            if len(arguments) > 2:
                raise ValueError("Usage: /export [md|json|txt] [new file]")
            format_name = arguments[0] if arguments else "md"
            if format_name not in {"md", "json", "txt"}:
                raise ValueError("Export format must be md, json or txt")
            record = self.current()
            # Export conversation data, never run configuration or credentials.
            if format_name == "json":
                rendered = json.dumps(
                    {"format": "nexus-studio-memory/v1", "conversation": record},
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                prefix = "## " if format_name == "md" else ""
                rendered = "\n\n".join(
                    f"{prefix}{message.get('role', 'message')}\n\n{message.get('content', '')}"
                    for message in record.get("messages", [])
                )
            if len(arguments) == 2:
                path = Path(arguments[1]).expanduser()
                # Exclusive creation refuses existing files and symlinks. Keep
                # private conversation exports readable only by this OS user.
                write_private_file(path, (rendered + "\n").encode("utf-8"))
                print(f"Exported conversation to {path}")
            else:
                print(rendered)
        elif command == "/upload":
            if not arguments:
                raise ValueError("Usage: /upload <local file> [message]")
            if len(self.artifact_ids) >= 5:
                raise ValueError("Attach at most five saved Studio files")
            saved = self.files.upload(arguments[0])
            self.artifact_ids.append(saved["id"])
            self.pending_task = " ".join(arguments[1:]).strip() or (
                f"Please review the attached file: {saved['data']['filename']}"
            )
        elif command == "/artifacts":
            if arguments:
                raise ValueError("Usage: /artifacts")
            self.files.display(self.files.artifacts(self.current()))
        elif command == "/download":
            self.files.download(arguments, self.current())
        elif command == "/multiline":
            if arguments:
                raise ValueError("Usage: /multiline")
            if not self.enhanced_input:
                raise ValueError(
                    "Install nexus-arc[studio-terminal] and use a terminal without --plain"
                )
            self.multiline = not self.multiline
            print(
                "Multiline enabled; Alt+Enter sends." if self.multiline else "Multiline disabled."
            )
        elif command == "/agents":
            if arguments:
                raise ValueError("Usage: /agents")
            items = agent_choices(self.client)
            for item in items:
                state = "invalid definition" if item["invalid"] else "definition available"
                print(f"{item['id']} · {item['name']} · {item['provider']} · {state}")
            print(f"{len(items)} agent definitions; execution checks apply when a message is sent.")
        elif command == "/entrypoints":
            if arguments:
                raise ValueError("Usage: /entrypoints")
            display_entrypoints(self.client)
        elif command == "/clear":
            if arguments:
                raise ValueError("Usage: /clear")
            clear_terminal()
        elif command == "/retry":
            if arguments:
                raise ValueError("Usage: /retry")
            payload = self.retry.prepare(self.session_id)
            self.pending_retry, self.pending_task = payload, payload["task"]
