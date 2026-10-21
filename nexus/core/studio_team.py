"""Shared Studio policy reusing Nexus sessions, project ACLs and credential resolution.

The desktop server does not enable this policy. Shared mode requires explicit
hosts/origin, an existing Nexus login service, isolated state and a CLI worker image.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

from nexus.adapters.ai.agy_provider import (
    agy_print_args,
    agy_stream_input,
    parse_agy_stream_output,
)
from nexus.adapters.ai.base import split_model_effort
from nexus.core.models import AgentResult

ROLES = {"viewer": 0, "operator": 1, "builder": 2, "owner": 3}
# Subscription CLIs that run in the isolated runner_image. agy has no API-key mode.
WORKER_CLIS = {"codex", "claude-code", "agy"}
ACCOUNT_ONLY_PROVIDERS = {"agy"}


class TeamAccessError(PermissionError):
    pass


class NexusTeamPolicy:
    def __init__(
        self,
        *,
        project_key,
        public_url,
        login_url,
        bindings,
        manager=None,
        environment_resolver=None,
        runner_image=None,
        allowed_tools=None,
        decision_keys=None,
        embed_origins=None,
    ):
        parsed = urlparse(public_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Shared Studio requires an explicit HTTPS public origin")
        if urlparse(login_url).scheme != "https" or urlparse(login_url).netloc != parsed.netloc:
            raise ValueError("Serve the existing Nexus login under the same HTTPS origin as Studio")
        if (
            not project_key
            or not isinstance(bindings, dict)
            or not bindings
            or any(not isinstance(key, str) or role not in ROLES for key, role in bindings.items())
        ):
            raise ValueError("Configure project_key and explicit Nexus ID to role bindings")
        if "owner" not in bindings.values():
            raise ValueError("Configure at least one owner")
        if manager is None:
            from nexus.core.auth.access_domain import auth_enabled

            if not auth_enabled():
                raise ValueError(
                    "Shared Studio requires the existing NEXUS_AUTH_ENABLED configuration"
                )
            from nexus.core.auth.manager import auth_manager

            manager = auth_manager
        if environment_resolver is None:
            from nexus.core.auth.access_domain import build_execution_env

            environment_resolver = build_execution_env
        self.project_key, self.public_url, self.login_url = (
            project_key,
            public_url.rstrip("/"),
            login_url,
        )
        self.host = parsed.netloc
        self.bindings = dict(bindings)
        self.embed_origins = self._embed_origins(embed_origins)
        self.manager = manager
        self.environment_resolver = environment_resolver
        self.decision_keys = decision_keys or {}
        if not isinstance(self.decision_keys, dict):
            raise ValueError("Decision keys must map Nexus IDs to environment references")
        for identity, config in self.decision_keys.items():
            if (
                identity not in self.bindings
                or not isinstance(config, dict)
                or set(config) - {"backend", "key_env", "model"}
                or config.get("backend") not in {"openrouter", "opencode", "openai"}
                or (
                    "model" in config
                    and (
                        not isinstance(config["model"], str) or not 1 <= len(config["model"]) <= 200
                    )
                )
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(config.get("key_env") or ""))
            ):
                raise ValueError(
                    "Configure decision backend and key_env for an existing Nexus identity"
                )
        self.runner_image = runner_image
        self.allowed_tools = set(allowed_tools or ["text:wordcount", "text:grep", "data:query"])

    @staticmethod
    def _embed_origins(origins):
        if origins is None:
            return []
        if not isinstance(origins, list) or len(origins) > 10:
            raise ValueError("Configure up to ten explicit HTTPS embed origins")
        normalized = []
        for origin in origins:
            if not isinstance(origin, str):
                raise ValueError("Embed origins must be HTTPS origins")
            parsed = urlparse(origin)
            try:
                port = parsed.port
            except ValueError:
                raise ValueError("Invalid embed origin port") from None
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", parsed.hostname)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or port is not None
                and not 1 <= port <= 65535
            ):
                raise ValueError(
                    "Embed origins must be explicit HTTPS origins without credentials or paths"
                )
            value = "https://" + parsed.hostname.lower() + (f":{port}" if port else "")
            if value not in normalized:
                normalized.append(value)
        return normalized

    def principal(self, headers):
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except Exception:
            return None
        session_id = headers.get("X-Nexus-Session", "")
        if not session_id:
            for name in ("nexus_web_session", "nexus_session"):
                if name in cookie:
                    session_id = cookie[name].value
                    break
        if not re.fullmatch(r"[a-fA-F0-9]{32}", session_id):
            return None
        try:
            record = self.manager.get_session_and_setup_status(session_id)
            expires = datetime.fromisoformat(record.get("expires_at") or "")
            if (
                not record.get("exists")
                or record.get("status") not in {"oauth_done", "completed"}
                or expires.tzinfo is None
                or expires <= datetime.now(UTC)
            ):
                return None
            identity = str(record.get("nexus_id") or "")
            role = self.bindings.get(identity)
            if not role or not self.manager.has_project_access(identity, self.project_key):
                return None
            return {"nexus_id": identity, "role": role, "session_id": session_id}
        except Exception:
            return None

    def csrf(self, secret, principal):
        return hmac.new(
            secret.encode(), principal["session_id"].encode(), hashlib.sha256
        ).hexdigest()

    def authorize(self, principal, route, payload=None):
        if principal is None:
            raise TeamAccessError("Sign in through Nexus and obtain project access")
        required = "operator"
        if (
            route.startswith("/api/catalog/")
            or route in {"/api/agents", "/api/workflows"}
            or route.startswith(("/api/agents/", "/api/workflows/"))
            or route.endswith(("/update", "/apply"))
            or route.startswith("/api/workbench/versions/")
        ):
            required = "builder"
        if route.startswith(
            (
                "/api/catalog/connectors",
                "/api/catalog/entrypoints",
                "/api/catalog/externalagents",
                "/api/connectors/",
            )
        ):
            required = "owner"
        if route.startswith("/api/workbench/storage/"):
            required = "owner"
        if route.startswith("/api/workbench/onboarding/"):
            required = "owner"
        if route == "/api/workbench/campaigns/save":
            required = "builder"
        if route == "/api/workbench/guide":
            required = "viewer"
        if route.startswith("/api/studio/assistant/"):
            required = "viewer"
        if route.startswith("/api/studio/files/"):
            required = "builder"
        if ROLES[principal["role"]] < ROLES[required]:
            raise TeamAccessError(f"This action requires the {required} role")

    @staticmethod
    def visible(record, principal):
        owner = record.get("owner_id") or record.get("requester_nexus_id")
        return bool(principal and (principal["role"] == "owner" or owner == principal["nexus_id"]))

    def tool_allowed(self, name, studio):
        return (
            name in self.allowed_tools
            or name in studio.operations.tools
            or any(
                name in getattr(getattr(studio, capability, None), "names", {})
                for capability in ("data_tools", "research", "dynamic_embeds", "rulesets", "media", "assistant")
            )
        )

    def assert_execution(self, identity):
        if ROLES.get(self.bindings.get(identity), -1) < ROLES[
            "operator"
        ] or not self.manager.has_project_access(identity, self.project_key):
            raise TeamAccessError("Nexus project access was revoked")

    def environment(self, identity, provider):
        self.assert_execution(identity)
        values, error = self.environment_resolver(identity)
        if error:
            raise TeamAccessError("Nexus provider setup is not ready: " + error)
        allowed_accounts = {
            item.strip() for item in values.get("NEXUS_ACCOUNT_AUTH_PROVIDERS", "").split(",")
        }
        account_name = "claude" if provider == "claude-code" else provider
        keys = {
            "codex": "OPENAI_API_KEY",
            "claude-code": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "copilot": "COPILOT_GITHUB_TOKEN",
        }
        if (
            provider not in keys
            and provider not in ACCOUNT_ONLY_PROVIDERS
            or not values.get(keys.get(provider, ""))
            and account_name not in allowed_accounts
        ):
            raise TeamAccessError("Selected provider is not configured for this Nexus user")
        return dict(values)

    def providers(self, records, identity):
        try:
            values, error = self.environment_resolver(identity)
            if error:
                values = {}
        except Exception:
            values = {}
        accounts = set(values.get("NEXUS_ACCOUNT_AUTH_PROVIDERS", "").split(","))
        keys = {
            "codex": "OPENAI_API_KEY",
            "claude-code": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "copilot": "COPILOT_GITHUB_TOKEN",
        }
        result = []
        for original in records:
            item = dict(original)
            name = item["id"]
            account = "claude" if name == "claude-code" else name
            ready = bool(values.get(keys.get(name, "")) or account in accounts)
            if item["kind"] == "cli":
                ready = (
                    ready
                    and name in WORKER_CLIS
                    and bool(
                        self.runner_image
                        and re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", self.runner_image)
                        and shutil.which("docker")
                    )
                )
            item.update(
                configured=ready,
                status="Nexus user configuration" if ready else "Complete Nexus user/worker setup",
                setup="Use the existing Nexus sign-in and provider setup",
            )
            result.append(item)
        return result

    def provider(self, name, payload, builder=False):
        from nexus.adapters.registry import AdapterRegistry

        env = self.environment(payload["requester_nexus_id"], name)
        if name in {"openai", "claude"}:
            return AdapterRegistry().create_ai(
                name, api_key=env["OPENAI_API_KEY" if name == "openai" else "ANTHROPIC_API_KEY"]
            )
        if name not in WORKER_CLIS:
            raise ValueError(
                "Shared subscription workers support Codex, Claude Code and Antigravity"
            )
        return ContainerCLIProvider(name, env, self.runner_image, builder=builder)


def worker_failure(detail, prompt: str = "") -> str:
    """Name a failed worker run with the last few lines the CLI reported.

    Codex echoes the prompt on stderr, so lines from the prompt are left out.
    """
    if isinstance(detail, bytes):
        detail = detail.decode(errors="replace")
    echoed = {line.strip() for line in prompt.splitlines()}
    lines = [
        line.strip()
        for line in str(detail or "").splitlines()
        if line.strip() and line.strip() not in echoed
    ]
    tail = " | ".join(lines[-3:])[-300:]
    return "Isolated provider execution failed" + (f": {tail}" if tail else "")


# Tools a shared CLI worker may use. "web" adds search and page fetch; "shell" also
# lets the CLI run commands inside the worker container, which can read the
# user's mounted CLI login, so it is an explicit choice.
WORKER_TOOL_LEVELS = ("none", "web", "shell")
_CLAUDE_TOOLS = {
    "none": [],
    "web": ["WebSearch", "WebFetch"],
    "shell": ["WebSearch", "WebFetch", "Bash", "Read", "Glob", "Grep"],
}


def worker_tool_level(value) -> str:
    level = str(value or "none").strip().lower()
    if level not in WORKER_TOOL_LEVELS:
        raise ValueError("Tools must be one of " + ", ".join(WORKER_TOOL_LEVELS))
    return level


def _activity(text) -> str:
    return "\n› " + " ".join(str(text).split())[:240] + "\n"


def _tool_activity(name, arguments) -> str:
    arguments = arguments if isinstance(arguments, dict) else {}
    detail = next(
        (
            arguments[key]
            for key in ("query", "url", "command", "pattern", "file_path", "path")
            if arguments.get(key)
        ),
        "",
    )
    return _activity(f"{name}: {detail}" if detail else str(name))


class ClaudeStream:
    """Turn Claude Code stream-json events into text deltas and tool activity."""

    def __init__(self, on_delta):
        self.on_delta, self.result = on_delta, None

    def feed(self, event):
        if event.get("type") == "result":
            self.result = event
        elif event.get("parent_tool_use_id"):
            return
        elif event.get("type") == "stream_event":
            delta = (event.get("event") or {}).get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                self.on_delta(delta["text"])
        elif event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self.on_delta(_tool_activity(block.get("name"), block.get("input")))


class CodexStream:
    """Turn ``codex exec --json`` events into messages and tool activity."""

    def __init__(self, on_delta):
        self.on_delta, self.output, self.error = on_delta, "", None

    def feed(self, event):
        item = event.get("item") or {}
        kind = item.get("type")
        if event.get("type") == "item.completed" and kind == "agent_message":
            self.output = str(item.get("text") or "")
            self.on_delta(self.output + "\n")
        elif event.get("type") == "item.completed" and kind == "web_search":
            self.on_delta(_activity(f"Web search: {item.get('query') or ''}"))
        elif event.get("type") == "item.started" and kind == "command_execution":
            self.on_delta(_activity(f"Run: {item.get('command') or ''}"))
        elif event.get("type") in {"turn.failed", "error"}:
            error = event.get("error")
            self.error = str(
                (error.get("message") if isinstance(error, dict) else error)
                or event.get("message")
                or "Codex turn failed"
            )


class ContainerCLIProvider:
    """Run a user's CLI with only its workspace and scoped credential homes mounted."""

    def __init__(self, name, environment, image, *, builder=False):
        self.name, self.environment, self.image, self.builder = (
            name,
            dict(environment),
            image,
            builder,
        )

    async def check_availability(self):
        if not self.image or not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", self.image):
            raise ValueError("Shared CLI runs require a pinned NEXUS_STUDIO_RUNNER_IMAGE digest")
        return bool(shutil.which("docker"))

    async def execute_agent(self, context):
        return await self._run(context)

    async def execute_agent_streaming(self, context, on_delta):
        return await self._run(context, on_delta)

    async def _run(self, context, on_delta=None):
        if not await self.check_availability():
            raise ValueError("Isolated worker is unavailable")
        if self.name not in WORKER_CLIS:
            raise ValueError("Unsupported shared CLI worker provider")
        docker = shutil.which("docker")
        if not docker:
            raise ValueError("Docker is required for isolated shared subscription runs")
        tools = worker_tool_level((context.metadata or {}).get("worker_tools"))
        attachment_directory = (context.metadata or {}).get("attachment_directory")
        attachment_mount = []
        claude_tools = list(_CLAUDE_TOOLS[tools])
        claude_allowed = list(claude_tools)
        if attachment_directory:
            path = Path(attachment_directory).resolve()
            if not path.is_dir() or "," in str(path):
                raise ValueError("Attachment files are unavailable")
            attachment_mount = [
                "--mount",
                f"type=bind,src={path},dst=/workspace/attachments,readonly",
            ]
            if "Read" not in claude_tools:
                claude_tools.append("Read")
                claude_allowed.append("Read(./attachments/**)")
        # Antigravity returns its answer in one piece, so it never streams.
        streaming = on_delta is not None and self.name != "agy"
        container_name = "nexus-studio-" + uuid.uuid4().hex
        client_env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "LANG", "TMPDIR"}
        }
        container_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/home/nexus"}
        command = [
            docker,
            "run",
            # Keep stdin open: the prompt is piped to the CLI, never put in argv.
            "-i",
            "--rm",
            "--pull",
            "never",
            "--name",
            container_name,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=256",
            "--memory=2g",
            "--cpus=2",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m",
            "--workdir",
            "/workspace",
        ]
        mounts = {"HOME": "/home/nexus"}
        if self.name == "codex":
            mounts["CODEX_HOME"] = "/run/credentials/codex"
        elif self.name == "claude-code":
            mounts["CLAUDE_HOME"] = "/run/credentials/claude"
        else:
            # Antigravity keeps its login and state in $HOME/.gemini under the
            # user's dedicated AGY_HOME, so that home becomes the container HOME.
            mounts = {"AGY_HOME": "/home/nexus"}
        for key, destination in mounts.items():
            source = self.environment.get(key)
            if not source:
                continue
            path = Path(source).resolve()
            if not path.is_dir() or "," in str(path):
                raise ValueError("Nexus user credential home is unavailable")
            command += ["--mount", f"type=bind,src={path},dst={destination}"]
            container_env[key] = destination
        # Existing Nexus login prepares HOME/.claude. Keep the official CLI's
        # normal location rather than redirecting it to the legacy CLAUDE_HOME.
        secret_keys = {
            "codex": ["OPENAI_API_KEY"],
            "claude-code": ["ANTHROPIC_API_KEY"],
            "agy": [],
        }[self.name]
        for key, value in container_env.items():
            command += ["--env", f"{key}={value}"]
        for key in secret_keys:
            if self.environment.get(key):
                client_env[key] = self.environment[key]
                command += ["--env", key]
        with tempfile.TemporaryDirectory(prefix="nexus-team-prompt-") as scratch:
            # Shared inference gets no project/state directory. Local coding execution
            # remains available only in the desktop runtime.
            command += [
                "--mount",
                f"type=bind,src={scratch},dst=/workspace,readonly",
                *attachment_mount,
                self.image,
            ]
            if self.name == "codex":
                command += [
                    "codex",
                    "exec",
                    "--skip-git-repo-check",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--ephemeral",
                ]
                # The container is the sandbox for "shell": read-only root, no
                # capabilities, an empty read-only workspace.
                command += (
                    ["--dangerously-bypass-approvals-and-sandbox"]
                    if tools == "shell"
                    else ["--sandbox", "read-only"]
                )
                if tools != "none":
                    command += ["-c", 'web_search="live"']
                if streaming:
                    command.append("--json")
            elif self.name == "agy":
                model, effort = split_model_effort(context.model_override, context.effort)
                timeout = int(context.timeout or 600)
                # --sandbox restricts terminal tools; the workspace is an empty read-only dir.
                command += ["agy", *agy_print_args(timeout, model, effort), "--sandbox"]
            else:
                command += [
                    "claude",
                    "-p",
                    "--output-format",
                    "stream-json" if streaming else "json",
                    *(["--verbose", "--include-partial-messages"] if streaming else []),
                    "--no-session-persistence",
                    "--setting-sources",
                    "",
                    "--tools",
                    ",".join(claude_tools),
                    *(["--allowedTools", ",".join(claude_allowed)] if claude_allowed else []),
                    "--strict-mcp-config",
                    "--mcp-config",
                    '{"mcpServers":{}}',
                ]
            if context.model_override and self.name != "agy":
                command += ["--model", context.model_override]
            if context.effort and self.name != "agy":
                if self.name == "codex":
                    command += ["-c", f'model_reasoning_effort="{context.effort}"']
                elif self.name == "claude-code":
                    command += ["--effort", context.effort]
            if self.name == "codex":
                command.append("-")
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=client_env,
                    limit=1 << 24,  # one stream event per line; the last repeats the reply
                )
                prompt_bytes = (
                    agy_stream_input(context.prompt)
                    if self.name == "agy"
                    else context.prompt.encode()
                )
                started = time.monotonic()
                if streaming:
                    return await asyncio.wait_for(
                        self._stream(process, prompt_bytes, on_delta, context.prompt),
                        timeout=context.timeout,
                    )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt_bytes), timeout=context.timeout
                )
                if self.name == "agy":
                    return parse_agy_stream_output(
                        stdout,
                        process.returncode,
                        stderr,
                        time.monotonic() - started,
                        model,
                    )
                text = stdout.decode(errors="replace")
                success = process.returncode == 0
                detail = stderr
                if self.name == "claude-code":
                    try:
                        parsed = json.loads(text or "{}")
                    except ValueError:
                        parsed = {"is_error": True}
                    success = success and not parsed.get("is_error")
                    text = str(parsed.get("result") or "")
                    if parsed.get("is_error") and text:
                        detail = text
                return AgentResult(
                    success=success,
                    output=text,
                    error=None if success else worker_failure(detail, context.prompt),
                    provider_used=self.name,
                )
            except TimeoutError:
                return AgentResult(
                    success=False,
                    output="",
                    error="Isolated provider timed out",
                    provider_used=self.name,
                )
            finally:
                if process and process.returncode is None:
                    process.kill()
                    await process.wait()
                cleanup = await asyncio.create_subprocess_exec(
                    docker,
                    "rm",
                    "--force",
                    container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=client_env,
                )
                try:
                    await asyncio.wait_for(cleanup.wait(), timeout=10)
                except TimeoutError:
                    cleanup.kill()
                    await cleanup.wait()

    async def _stream(self, process, prompt_bytes, on_delta, prompt):
        stderr = asyncio.create_task(process.stderr.read())
        process.stdin.write(prompt_bytes)
        await process.stdin.drain()
        process.stdin.close()
        parser = (ClaudeStream if self.name == "claude-code" else CodexStream)(on_delta)
        while line := await process.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                parser.feed(event)
        returncode = await process.wait()
        detail = await stderr
        if self.name == "claude-code":
            result = parser.result or {}
            output = str(result.get("result") or "")
            success = returncode == 0 and bool(result) and not result.get("is_error")
            if result.get("is_error") and output:
                detail = output
        else:
            output = parser.output
            success = returncode == 0 and not parser.error
            detail = parser.error or detail
        return AgentResult(
            success=success,
            output=output,
            error=None if success else worker_failure(detail, prompt),
            provider_used=self.name,
        )


def load_team_configuration(path):
    """Resolve an administrator-owned configuration, with secret references only."""
    from nexus.adapters.transport import create_transport

    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Team configuration must be an object")
    policy = NexusTeamPolicy(
        **{
            key: config[key]
            for key in (
                "project_key",
                "public_url",
                "login_url",
                "bindings",
                "runner_image",
                "allowed_tools",
                "decision_keys",
                "embed_origins",
            )
            if key in config
        }
    )
    broker = config.get("broker")
    if not isinstance(broker, dict) or broker.get("kind") not in {"mqtt", "kafka", "memory"}:
        raise ValueError("Choose an explicit MQTT/Kafka broker, or local in-process events")
    options = {}
    raw_options = broker.get("options", {})
    if not isinstance(raw_options, dict):
        raise ValueError("Broker options must be an object")
    for key, value in raw_options.items():
        if key in {"password", "sasl_plain_password", "ssl_password"} and not isinstance(
            value, dict
        ):
            raise ValueError("Broker credentials require environment references")
        if isinstance(value, dict):
            if set(value) != {"env"} or not isinstance(value["env"], str):
                raise ValueError("Secret references use {env: VARIABLE_NAME}")
            value = os.getenv(value["env"])
            if not value:
                raise ValueError("Required broker environment variable is not configured")
        options[key] = value
    if broker["kind"] == "mqtt" and options.get("tls") is not True:
        raise ValueError("Shared MQTT requires verified TLS")
    if broker["kind"] == "kafka" and (
        options.get("security_protocol") not in {"SSL", "SASL_SSL"}
        or options.get("ssl_check_hostname") is False
    ):
        raise ValueError("Shared Kafka requires verified SSL/SASL_SSL")
    return policy, create_transport(broker["kind"], **options)
