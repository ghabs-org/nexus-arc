"""Optional terminal presentation; redirected output and base installs stay plain."""

import shlex
import sys

from nexus.core.studio_terminal_agents import agent_choices


def render_markdown(text, *, plain=False, label=None):
    if not plain and sys.stdout.isatty():
        try:
            from rich.console import Console
            from rich.markdown import Markdown
        except ImportError:
            pass
        else:
            if label:
                print(label)
            Console(file=sys.stdout, markup=False, highlight=False, emoji=False).print(
                Markdown(str(text), hyperlinks=False)
            )
            return
    print(f"{label} {text}" if label else text)


def completion_candidates(text, sessions):
    """Return replacement text/length pairs without submitting any server action.

    The caller invokes this only for explicit completion. Results are not saved
    to disk or shared between prompts, accounts or Studio instances.
    """
    command, space, argument = text.partition(" ")
    if not space:
        try:
            aliases = {"/" + name for name in sessions.shortcuts.aliases()}
        except (ValueError, OSError):
            return []
        return [
            (item, len(command))
            for item in sorted(sessions.commands | {"/quit", "/exit"} | aliases)
            if item.startswith(command) and command.startswith("/")
        ]
    if command not in {"/switch", "/delete", "/download", "/export", "/agent"}:
        return []
    # A completed quoted first argument or any second argument ends completion.
    # An unfinished quote is normal while typing a title containing spaces.
    try:
        arguments = shlex.split(argument)
    except ValueError:
        prefix = argument.lstrip().lstrip("\"'")
    else:
        if command not in {"/switch", "/delete"} and (
            len(arguments) > 1 or (arguments and argument.endswith(" "))
        ):
            return []
        prefix = " ".join(arguments) if arguments else ""
    try:
        if command in {"/switch", "/delete"}:
            items = sessions.visible_sessions()
            values = [value for item in items for value in (item["id"], item.get("title", ""))]
        elif command == "/agent":
            items = agent_choices(sessions.client)
            values = [
                value
                for item in items
                if not item["invalid"]
                for value in (item["id"], item["name"])
            ]
        elif command == "/download":
            if not sessions.session_id:
                return []
            items = sessions.files.artifacts(sessions.current())
            values = [
                value
                for item in items
                for value in (f"{item['id']}:{item['revision']}", item["data"]["filename"])
            ]
        else:
            values = ["md", "json", "txt"]
    except (ValueError, OSError):
        # Missing/revoked access offers no names and never falls back to an
        # unfiltered global catalog. Manual commands still report normal errors.
        return []
    return [
        (shlex.quote(value), len(argument))
        for value in sorted(set(values))
        if value and value.casefold().startswith(prefix.casefold())
    ]


def make_completer(sessions):
    """Load prompt-toolkit only when the enhanced terminal is selected."""
    from prompt_toolkit.completion import Completer, Completion

    class StudioCompleter(Completer):
        def get_completions(self, document, complete_event):
            if not complete_event.completion_requested:
                return
            for value, length in completion_candidates(document.text_before_cursor, sessions):
                yield Completion(value, start_position=-length)

    return StudioCompleter()


class TerminalInput:
    """Keep line history in memory, with no shell execution or history files."""

    def __init__(self, sessions, *, plain=False):
        self.sessions = sessions
        self.prompt_session = None
        if not plain and sys.stdin.isatty() and sys.stdout.isatty():
            try:
                from prompt_toolkit import PromptSession
            except ImportError:
                return
            self.prompt_session = PromptSession(
                completer=make_completer(sessions),
                complete_while_typing=False,
                complete_in_thread=True,
                enable_system_prompt=False,
                enable_open_in_editor=False,
            )

    def read(self):
        if self.prompt_session is not None:
            return self.prompt_session.prompt(
                "You: ",
                multiline=self.sessions.multiline,
                bottom_toolbar=(
                    "Multiline: Alt+Enter sends · /multiline toggles off"
                    if self.sessions.multiline
                    else None
                ),
            )
        if self.sessions.multiline:
            raise ValueError("Multiline editing requires an enhanced terminal")
        return input("You: ")
