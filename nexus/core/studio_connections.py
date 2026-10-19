"""Explicit, identity-scoped integration actions for Studio and workflow tools."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import inspect
import json
import os
import re
from dataclasses import asdict, is_dataclass
from urllib.parse import urlparse

execution_identity = contextvars.ContextVar("studio_execution_identity", default=None)
NATIVE = {
    "github",
    "gitlab",
    "telegram",
    "discord",
    "slack",
    "openclaw",
    "mcp",
    "sql",
    "openapi",
    "bedrock",
    "bedrock-kb",
    "bedrock-flow",
    "mongodb",
    "nuclia",
    "powerbi",
}
READ_ACTIONS = {
    "list_operations",
    "read",
    "query",
    "inspect_schema",
    "list_issues",
    "get_issue",
    "comments",
    "linked_prs",
    "list_prs",
    "get_pr",
    "discover",
    "status",
}
WRITE_ACTIONS = {
    "invoke",
    "execute",
    "create_issue",
    "update_issue",
    "comment",
    "close_issue",
    "create_branch",
    "merge",
    "send",
    "update_message",
    "call",
    "create_pr",
    "pr_comment",
}


def env_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", value):
        raise ValueError("Use an environment variable name, never a credential value")
    return value


def https_url(value):
    parsed = urlparse(str(value))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an HTTPS origin without credentials, query or fragment")
    return str(value).rstrip("/")


def validate_connection(data):
    kind = data["type"]
    from nexus.core.studio_enterprise import KINDS, validate_enterprise

    if kind in KINDS:
        return validate_enterprise(data)
    if kind == "openapi":
        from nexus.core.studio_openapi import validate_openapi

        return validate_openapi(data)
    if kind == "sql":
        from nexus.core.studio_sql_connectors import validate_sql

        return validate_sql(data)
    result = {"type": kind, "enabled": data.get("enabled", False)}
    if type(result["enabled"]) is not bool:
        raise ValueError("Enabled must be true or false")
    result["token_env"] = env_name(data["token_env"]) if data.get("token_env") else ""
    if kind in {"github", "gitlab"}:
        repo = str(data.get("repo") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", repo) or ".." in repo.split(
            "/"
        ):
            raise ValueError("Repository must be an owner/project path")
        result.update(
            repo=repo,
            base_url=https_url(
                data.get("base_url")
                or ("https://github.com" if kind == "github" else "https://gitlab.com")
            ),
        )
        if kind == "github" and result["base_url"] != "https://github.com":
            raise ValueError("This GitHub adapter currently supports github.com")
    elif kind == "mcp":
        transport = data.get("transport", "stdio")
        if transport not in {"stdio", "sse", "streamable-http"}:
            raise ValueError("Choose stdio, sse or streamable-http MCP transport")
        if transport != "stdio":
            from nexus.core.studio_remote import validate_remote

            result.update(validate_remote(data))
        result["transport"] = transport
        command = data.get("command")
        args = data.get("args", [])
        if transport == "stdio" and (
            not isinstance(command, str)
            or not command
            or len(command) > 1000
            or any(c in command for c in "\n\r\x00")
        ):
            raise ValueError("Choose a trusted installed MCP executable")
        if (
            not isinstance(args, list)
            or len(args) > 50
            or any(not isinstance(arg, str) or len(arg) > 2000 for arg in args)
        ):
            raise ValueError("MCP arguments must be a list of at most 50 strings")
        refs = data.get("env", {})
        if not isinstance(refs, dict) or len(refs) > 30:
            raise ValueError("MCP environment must map names to environment references")
        result.update(
            command=command if transport == "stdio" else "",
            args=args,
            env={env_name(k): env_name(v) for k, v in refs.items()},
        )
        tools = data.get("tools", [])
        if (
            not isinstance(tools, list)
            or len(tools) > 100
            or any(not isinstance(v, str) or not v or len(v) > 200 for v in tools)
        ):
            raise ValueError("Choose at most 100 MCP tool names after discovery")
        result["tools"] = list(dict.fromkeys(tools))
        safe = data.get("read_only_tools", [])
        if not isinstance(safe, list) or any(t not in tools for t in safe):
            raise ValueError("Read-only MCP tools must be selected from the enabled tools")
        result["read_only_tools"] = list(dict.fromkeys(safe))
        if type(data.get("persistent", False)) is not bool:
            raise ValueError("Persistent MCP sessions must be true or false")
        result["persistent"] = data.get("persistent", False)
    else:
        channel = data.get("channel", "")
        if not isinstance(channel, str) or len(channel) > 200:
            raise ValueError("Channel must be a short identifier")
        result["channel"] = channel
        scope = data.get("credential_scope", "personal")
        if scope not in {"personal", "service"}:
            raise ValueError("Messaging credential scope must be personal or service")
        result["credential_scope"] = scope
        if kind == "openclaw":
            result["base_url"] = https_url(data.get("base_url"))
    # A connection only grants the actions explicitly selected by its owner.
    actions = data.get(
        "actions",
        ["discover"]
        if kind == "mcp"
        else ["list_issues"]
        if kind in {"github", "gitlab"}
        else ["status"],
    )
    if not isinstance(actions, list) or set(actions) - (READ_ACTIONS | WRITE_ACTIONS):
        raise ValueError("Unknown integration action")
    result["actions"] = list(dict.fromkeys(actions))
    return result


class StudioConnections:
    def __init__(self, studio, factories=None, enterprise_options=None):
        self.studio = studio
        self.factories = factories or {}
        from nexus.core.studio_sql_connectors import StudioSQLConnections

        self.sql = StudioSQLConnections(studio)
        from nexus.core.studio_enterprise import StudioEnterprise

        self.enterprise = StudioEnterprise(studio, **(enterprise_options or {}))
        from nexus.tools.mcp_sessions import MCPSessionPool

        self.mcp_sessions = MCPSessionPool()

    def close(self):
        self.sql.close()
        self.enterprise.close()
        self.mcp_sessions.close()

    def credentials(self, data, identity):
        if self.studio.team:
            self.studio.team.assert_execution(identity)
            if (
                data["type"] in {"telegram", "discord", "slack", "openclaw"}
                and data.get("credential_scope") == "service"
            ):
                token = os.getenv(data.get("token_env", ""))
                if not token:
                    raise ValueError("Service messaging credential is not configured")
                return token
            if data["type"] in {"github", "gitlab"}:
                from nexus.core.auth.credential_crypto import decrypt_secret
                from nexus.core.auth.credential_store import get_user_credentials

                record = get_user_credentials(identity)
                if record is None:
                    raise PermissionError("Connect your own Git account in Nexus account settings")
                if data["type"] == "gitlab":
                    from nexus.core.auth.access_domain import (
                        _gitlab_base_url,
                        _resolve_gitlab_access_token,
                    )

                    if data["base_url"] != _gitlab_base_url():
                        raise PermissionError(
                            "Connection host must match your Nexus GitLab sign-in host"
                        )
                    token, error = _resolve_gitlab_access_token(record)
                    if not token or error:
                        raise PermissionError(
                            "Reconnect your GitLab account in Nexus account settings"
                        )
                    return token
                encrypted = getattr(record, data["type"] + "_token_enc", None)
                if not encrypted:
                    raise PermissionError("Connect your own Git account in Nexus account settings")
                return decrypt_secret(encrypted)
            values, error = self.studio.team.environment_resolver(identity)
            if error or not data.get("token_env") or not values.get(data["token_env"]):
                raise PermissionError(
                    "This connection needs your own configured account credential"
                )
            return values[data["token_env"]]
        name = data.get("token_env") or {"github": "GITHUB_TOKEN", "gitlab": "GITLAB_TOKEN"}.get(
            data["type"], ""
        )
        value = os.getenv(name) if name else None
        if not value:
            raise ValueError("Configure this connection's credential environment reference")
        return value

    async def adapter(self, data, identity):
        if data["type"] in self.factories:
            result = self.factories[data["type"]](data, identity)
            return await result if inspect.isawaitable(result) else result
        token = self.credentials(data, identity)
        if data["type"] == "github":
            from nexus.adapters.git.github import GitHubPlatform

            return GitHubPlatform(repo=data["repo"], token=token)
        if data["type"] == "gitlab":
            from nexus.adapters.git.gitlab import GitLabPlatform

            return GitLabPlatform(repo=data["repo"], token=token, base_url=data["base_url"])
        if data["type"] == "slack":
            from nexus.adapters.notifications.slack import SlackNotificationChannel

            return SlackNotificationChannel(token=token, default_channel=data["channel"])
        if data["type"] == "discord":
            from nexus.adapters.notifications.discord import DiscordNotificationChannel

            return DiscordNotificationChannel(bot_token=token, alert_channel_id=data["channel"])
        if data["type"] == "telegram":
            from telegram import Bot

            return Bot(token=token)
        from nexus.adapters.notifications.openclaw import OpenClawNotificationChannel

        return OpenClawNotificationChannel(
            bridge_url=data["base_url"],
            auth_token=token,
            sender_id=data["channel"],
            channel="telegram",
        )

    def mcp_environment(self, data, identity):
        if self.studio.team:
            self.studio.team.assert_execution(identity)
            values, error = self.studio.team.environment_resolver(identity)
            if error:
                raise PermissionError("Personal MCP account setup is not ready")
        else:
            values = os.environ
        result = {"PATH": os.defpath}
        if self.studio.team:
            home = self.studio.state / "mcp-homes" / hashlib.sha256(identity.encode()).hexdigest()
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            result.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
        for target, source in data["env"].items():
            if not values.get(source):
                raise ValueError("An MCP environment reference is not configured for your account")
            result[target] = values[source]
        return result

    async def execute(
        self, identity, payload, actor=None, *, approved=False, probe=False, expected_data=None
    ):
        record = self.studio.catalog.get("connectors", identity)
        data = record["data"]
        if expected_data is not None and data != expected_data:
            raise ValueError("Connection changed before its check")
        if not data["enabled"] and not probe:
            raise ValueError("Connection is disabled")
        if not probe:
            self.studio.resource_lifecycle.assert_launch("connectors:" + identity)
        if self.studio.team:
            self.studio.team.assert_execution(actor)
        action = payload.get("action") or (
            "discover"
            if data["type"] == "mcp"
            else "list_issues"
            if data["type"] in {"github", "gitlab"}
            else "status"
        )
        if action not in data["actions"]:
            raise PermissionError("Enable this action in the connection settings first")
        if probe and action not in READ_ACTIONS:
            raise PermissionError("Connection checks only allow read-only actions")
        read_call = (
            data["type"] == "mcp"
            and action == "call"
            and payload.get("tool") in data.get("read_only_tools", [])
        )
        if (
            action in WRITE_ACTIONS
            and not read_call
            and not (approved or payload.get("confirm") is True)
        ):
            raise ValueError("Review the action and explicitly confirm its external effects")
        arguments = payload.get("arguments", {})
        if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 64000:
            raise ValueError("Action arguments must be an object up to 64 KiB")
        adapter = None
        try:
            from nexus.core.studio_enterprise import KINDS

            if data["type"] in KINDS:
                return await asyncio.to_thread(self.enterprise.call, data, actor, action, arguments)
            if data["type"] == "openapi":
                from nexus.core.studio_openapi import call_openapi

                return await asyncio.to_thread(
                    call_openapi, self.studio, data, actor, action, arguments
                )
            if data["type"] == "sql":
                return await asyncio.to_thread(self.sql.call, data, actor, action, arguments)
            if data["type"] == "mcp":
                from nexus.tools.mcp import call_server_tool, list_server_tools

                if action != "discover" and payload.get("tool") not in data["tools"]:
                    raise PermissionError("This MCP tool is not enabled on this connection")
                if data.get("transport", "stdio") != "stdio":
                    from nexus.core.studio_remote import (
                        remote_headers,
                        remote_request_url,
                        validate_destination,
                    )
                    from nexus.tools.mcp_remote import remote_mcp

                    validate_destination(data["url"], data.get("allow_private", False))
                    return await remote_mcp(
                        remote_request_url(self.studio, data, actor),
                        data["transport"],
                        headers=remote_headers(self.studio, data, actor),
                        tool=None if action == "discover" else payload.get("tool"),
                        arguments=arguments,
                    )
                env = self.mcp_environment(data, actor)
                if data.get("persistent"):

                    def authorize():
                        if self.studio.team:
                            self.studio.team.assert_execution(actor)
                        if self.studio.catalog.get("connectors", identity)["data"] != data:
                            raise PermissionError("MCP connection changed while waiting")

                    return await self.mcp_sessions.invoke(
                        (identity, actor),
                        data["command"],
                        data["args"],
                        env,
                        tool=None if action == "discover" else payload.get("tool"),
                        arguments=arguments,
                        authorize=authorize,
                    )
                if action == "discover":
                    return await asyncio.wait_for(
                        list_server_tools(data["command"], data["args"], env), 30
                    )
                tool = payload.get("tool")
                if tool not in data["tools"]:
                    raise PermissionError("This MCP tool is not enabled on this connection")
                return await asyncio.wait_for(
                    call_server_tool(data["command"], tool, arguments, data["args"], env), 60
                )
            adapter = await self.adapter(data, actor)
            if data["type"] in {"github", "gitlab"}:
                methods = {
                    "list_issues": "list_open_issues",
                    "get_issue": "get_issue",
                    "comments": "get_comments",
                    "linked_prs": "search_linked_prs",
                    "create_issue": "create_issue",
                    "update_issue": "update_issue",
                    "comment": "add_comment",
                    "close_issue": "close_issue",
                    "create_branch": "create_branch",
                    "merge": "merge_pull_request",
                    "list_prs": "list_pull_requests",
                    "get_pr": "get_pull_request",
                    "create_pr": "create_pull_request",
                    "pr_comment": "add_pull_request_comment",
                }
                if action not in methods:
                    raise ValueError("Choose a supported Git action")
                result = await asyncio.wait_for(getattr(adapter, methods[action])(**arguments), 60)
            elif action == "status":
                return {
                    "credential_configured": True,
                    "channel": data["channel"],
                    "authorization_verified": False,
                }
            elif action in {"send", "update_message"}:
                from nexus.adapters.notifications.base import Button, Message

                text = arguments.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                    raise ValueError("Message text must be nonempty, up to 16000 characters")
                raw_buttons = arguments.get("buttons", [])
                if (
                    not isinstance(raw_buttons, list)
                    or len(raw_buttons) > 5
                    or any(
                        not isinstance(b, dict)
                        or set(b) != {"label", "callback_data"}
                        or not isinstance(b["label"], str)
                        or not 1 <= len(b["label"]) <= 60
                        or not isinstance(b["callback_data"], str)
                        or not 1 <= len(b["callback_data"]) <= 64
                        for b in raw_buttons
                    )
                ):
                    raise ValueError("Use at most five short callback buttons")
                buttons = [Button(**b) for b in raw_buttons] or None
                if data["type"] == "telegram":
                    if data["type"] not in self.factories:
                        await asyncio.wait_for(adapter.initialize(), 30)
                    method = adapter.send_message if action == "send" else adapter.edit_message_text
                    kwargs = {"chat_id": data["channel"], "text": text}
                    if buttons:
                        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                        kwargs["reply_markup"] = InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(b.label, callback_data=b.callback_data)
                                    for b in buttons
                                ]
                            ]
                        )
                    if action == "update_message":
                        kwargs["message_id"] = arguments["message_id"]
                    message = await method(**kwargs)
                    result = {"message_id": str(message.message_id)}
                else:
                    result = (
                        await adapter.send_message(
                            data["channel"], Message(text=text, buttons=buttons)
                        )
                        if action == "send"
                        else await adapter.update_message(str(arguments["message_id"]), text)
                    )
            else:
                raise ValueError("Choose a supported messaging action")

            def serialize(value):
                if is_dataclass(value):
                    return asdict(value)
                if isinstance(value, list):
                    return [serialize(item) for item in value]
                return value

            return serialize(result)
        except PermissionError:
            raise
        except Exception:
            raise ValueError(
                "Integration action failed. Check your account, permissions and recorded remote outcome before retrying"
            ) from None
        finally:
            if adapter is not None and data["type"] not in self.factories:
                close = getattr(adapter, "aclose", None) or (
                    getattr(adapter, "shutdown", None) if data["type"] == "telegram" else None
                )
                if close:
                    try:
                        await asyncio.wait_for(close(), 10)
                    except Exception:
                        pass  # Do not replace a recorded send outcome with cleanup failure.

    def tool(self, identity):
        async def invoke(action=None, arguments=None, tool=None, confirm=False):
            # A model cannot self-authorize an external mutation with confirm=True.
            data = self.studio.catalog.get("connectors", identity)["data"]
            read_call = (
                data["type"] == "mcp"
                and action == "call"
                and tool in data.get("read_only_tools", [])
            )
            if action in WRITE_ACTIONS and not read_call:
                raise PermissionError(
                    "Use an approved integration workflow node or explicit Studio action for mutations"
                )
            return await self.execute(
                identity,
                {"action": action, "arguments": arguments or {}, "tool": tool},
                execution_identity.get(),
            )

        return invoke
