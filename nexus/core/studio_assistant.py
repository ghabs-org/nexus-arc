"""Local Studio help and read-only tools shared by UI and configured agents."""

import hashlib
import re

from nexus.core.studio_connections import execution_identity

TOPICS = (
    (
        "storage",
        "Where agents and workflows are saved",
        "#files",
        "Agent definitions are YAML files in <workspace>/agents/<id>.yaml. Workflow definitions are in <workspace>/workflows/<id>.yaml. The server workspace is selected by nexus studio --dir. On a headless VM these paths belong to the VM, not the browser computer. Use Agents or Workflows to edit validated definitions. Workspace files can preview or copy them without SSH.",
    ),
    (
        "files",
        "Browse files on a VM",
        "#files",
        "The browser file picker sees your computer. Workspace files browses only configured server folders. Agents and workflows are read-only here; files/ accepts create-only uploads. Extra roots require administrator configuration. Save a private copy to download, attach to chat or add to a knowledge project. No file content is sent to AI merely by browsing. Workspace folders are shared with authorized builders; use private Library artifacts for personal documents.",
    ),
    (
        "projects",
        "Knowledge projects and server folders",
        "#projects",
        "Knowledge projects group instructions, document revisions and related conversations. They are not host directories or source repositories. To use a server file, save a private artifact copy in Workspace files, then add that saved file to a knowledge project. Search defaults to local lexical retrieval; embedding/API use depends on explicit configuration.",
    ),
    (
        "state",
        "Conversations and operational state",
        "#workspace",
        "Private state defaults to <workspace>/.nexus/studio, or the separately configured --state-dir. Shared mode requires private state outside the workspace. Configured SQL state can store operational records in a database. Definition YAML stays in agents/ and workflows/. Binary artifacts use the selected file or object store. Workspace files does not expose private state or credentials.",
    ),
    (
        "providers",
        "Subscriptions and API billing",
        "#providers",
        "Choose a configured provider under Providers & models. Codex and Claude Code CLI support uses the server-side configured account or isolated user runner. API integrations and model APIs have their own billing. Studio never silently changes providers. The Studio assistant's local help costs no model usage; Ask AI explicitly invokes the selected provider.",
    ),
    (
        "build",
        "Build agents and workflows",
        "#quick-build",
        "Quick Build creates a proposal from a use case. Review components, dependencies and revision changes before applying or activating. Credential-dependent components need setup and explicit enabling. Existing Agents and Workflows screens provide editors and version history. Unsupported configuration constructs must be reported before saving.",
    ),
    (
        "schedules",
        "Schedules on a server",
        "#schedules",
        "Schedules need a running Studio runtime. A headless VM can keep running after the browser closes. Closing the portal does not stop server jobs. A stopped or sleeping host cannot execute scheduled tasks. Inspect schedule history, time zones and explicit enabling in Schedules.",
    ),
)


class StudioAssistant:
    def __init__(self, studio):
        from nexus.tools import tool_registry

        self.studio = studio
        prefix = "studio:" + hashlib.sha256(str(studio.state).encode()).hexdigest()[:12] + ":"

        async def help_tool(question=""):
            return self.answer(question, self.actor())

        async def list_tool(root="files", path=""):
            return studio.workspace_files.listing(root, path, self.actor())

        async def read_tool(root="files", path=""):
            return {"untrusted_file": studio.workspace_files.preview(root, path, self.actor())}

        self.names = {
            prefix + "studio_help": help_tool,
            prefix + "workspace_list": list_tool,
            prefix + "workspace_read": read_tool,
        }
        descriptions = [
            "Read built-in Studio documentation and current agent/workflow names. Argument: question. No file contents, secrets, network or mutations.",
            "List one configured server workspace folder. Arguments: root (default files), path (relative directory). Builder permission required. No arbitrary host paths.",
            "Read a bounded untrusted preview of an explicitly requested server file. Arguments: root, path. Builder permission required. No writes or execution.",
        ]
        for (name, function), description in zip(self.names.items(), descriptions):
            tool_registry.register(
                name, function, category="Studio assistance", description=description
            )

    def actor(self):
        if not self.studio.team:
            return None
        identity = execution_identity.get()
        self.studio.team.assert_execution(identity)
        return {"nexus_id": identity, "role": self.studio.team.bindings[identity]}

    def authorize(self, actor):
        if self.studio.team:
            identity = (actor or {}).get("nexus_id")
            if (
                identity not in self.studio.team.bindings
                or not self.studio.team.manager.has_project_access(
                    identity, self.studio.team.project_key
                )
            ):
                raise PermissionError("Studio help requires Nexus project access")

    def answer(self, question="", actor=None):
        self.authorize(actor)
        if not isinstance(question, str) or len(question) > 2000:
            raise ValueError("Ask a Studio question up to 2000 characters")
        terms = set(re.findall(r"[a-z0-9]+", question.casefold())) - {
            "the",
            "a",
            "are",
            "is",
            "to",
            "my",
            "in",
            "and",
            "where",
            "how",
            "what",
        }
        ranked = sorted(
            enumerate(TOPICS),
            key=lambda pair: (
                -len(terms & set(re.findall(r"[a-z0-9]+", " ".join(pair[1]).casefold())))
            ),
        )
        chosen = [topic for _, topic in ranked[:3]] if question.strip() else list(TOPICS)
        return {
            "mode": "local-help",
            "question": question,
            "answer": "\n\n".join(topic[3] for topic in chosen),
            "sources": [{"id": topic[0], "title": topic[1], "url": topic[2]} for topic in chosen],
            "workspace": {
                "name": self.studio.root.name,
                "path": str(self.studio.root),
                "agents": str(self.studio.root / "agents"),
                "workflows": str(self.studio.root / "workflows"),
            },
            "resources": {
                kind: [
                    {
                        "id": item["id"],
                        "name": item.get("document", {})
                        .get("metadata", {})
                        .get("name", item["id"]),
                        "url": "#" + kind,
                    }
                    for item in self.studio.definitions(kind)[:50]
                ]
                for kind in ("agents", "workflows")
            },
            "notice": "Built-in help and live metadata only. No model call or file-content upload.",
        }

    def spec(self):
        return {
            "purpose": "You are the read-only Nexus Studio assistant. Use the studio_help tool for verified platform facts and live workspace metadata. Cite its navigation links. Distinguish server paths from browser files and knowledge projects. Do not invent configuration, claim changes or request secrets. You cannot modify files, run shell commands or activate resources. File contents require the user's explicit attachment.",
            "tool_ids": [next(name for name in self.names if name.endswith(":studio_help"))],
        }

    def inventory(self):
        return {
            "items": [
                {
                    "id": name,
                    "configured": True,
                    "billing": "Local read-only processing; selected AI usage is separate",
                    "setup": "Built in; workspace file tools require builder permission",
                }
                for name in self.names
            ]
        }

    def close(self):
        from nexus.tools import tool_registry

        for name, function in self.names.items():
            tool_registry.unregister(name, func=function)
