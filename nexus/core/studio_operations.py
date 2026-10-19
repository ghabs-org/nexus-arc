"""Local evaluation, scheduled execution and configured integration operations."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import sqlite3
import threading
import time
import urllib.request
from datetime import UTC, datetime
from urllib.parse import urlparse

from nexus.core.studio_catalog import _text

OPERATION_KINDS = ("evaluations", "schedules", "connectors", "entrypoints")


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Include a timezone")
        return parsed.astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise ValueError("Use an ISO date/time including timezone") from exc


class StudioOperations:
    def __init__(self, studio):
        self.studio = studio
        self.stop_event = threading.Event()
        self.worker = None
        self.tools = {}
        self.reconcile_connectors()

    def target(self, data, targets=None):
        kind = data.get("target_kind")
        if kind not in {"agent", "workflow"}:
            raise ValueError("Choose an agent or workflow target")
        identity = _text(data.get("target_id"), "Target ID", 96)
        collection = "agents" if kind == "agent" else "workflows"
        if targets is None or (collection, identity) not in targets:
            self.studio.definition(collection, identity)
        return {"target_kind": kind, "target_id": identity}

    def validate(self, kind, data, targets=None):
        if not isinstance(data, dict):
            raise ValueError("Configuration must be an object")
        if kind == "externalagents":
            from nexus.core.studio_external import validate_external

            return validate_external(data)
        if kind == "models":
            from nexus.core.studio import _PROVIDERS

            provider = data.get("provider")
            if provider not in {p[0] for p in _PROVIDERS}:
                raise ValueError("Unknown model profile provider")
            return {"provider": provider, "model": _text(data.get("model"), "Model ID", 200)}
        if kind == "evaluations":
            target = self.target(data, targets)
            cases = data.get("cases")
            if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
                raise ValueError("Add between 1 and 100 evaluation cases")
            from nexus.core.studio_evaluations import validate_case

            normalized = [validate_case(case) for case in cases]
            return {**target, "cases": normalized}
        if kind == "schedules":
            target = self.target(data, targets)
            enabled = data.get("enabled", False)
            if type(enabled) is not bool:
                raise ValueError("Enabled must be true or false")
            interval = data.get("interval_seconds", 0)
            if type(interval) is not int or interval not in {0} and not 60 <= interval <= 31536000:
                raise ValueError("Interval must be 0 (once), or between 60 seconds and one year")
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            timezone = data.get("timezone", "UTC")
            try:
                if not isinstance(timezone, str) or len(timezone) > 100:
                    raise ValueError
                ZoneInfo(timezone)
            except (ValueError, TypeError, ZoneInfoNotFoundError):
                raise ValueError("Use a valid IANA timezone") from None
            calendar = {"timezone": timezone}
            if data.get("cron"):
                from nexus.core.studio_calendar import next_occurrence

                if interval:
                    raise ValueError("Choose cron or interval, not both")
                calendar = {"cron": data["cron"], "timezone": timezone}
                initial = (
                    data.get("run_at")
                    or next_occurrence(data["cron"], timezone, datetime.now(UTC)).isoformat()
                )
                # Validate even when an initial date was supplied.
                next_occurrence(data["cron"], timezone, _timestamp(initial))
            else:
                initial = data.get("run_at")
            return {
                **target,
                **calendar,
                "enabled": enabled,
                "task": _text(data.get("task"), "Task", 16000),
                "run_at": _timestamp(initial).isoformat(),
                "interval_seconds": interval,
            }
        if kind == "entrypoints":
            target = self.target(data, targets)
            env = _text(data.get("token_env"), "Bearer token environment variable", 96)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env):
                raise ValueError("Use an environment variable name, not the secret value")
            enabled = data.get("enabled", False)
            if type(enabled) is not bool:
                raise ValueError("Enabled must be true or false")
            result = {**target, "token_env": env, "enabled": enabled}
            a2a = data.get("a2a_enabled", False)
            if type(a2a) is not bool or (a2a and data.get("type", "webhook") != "webhook"):
                raise ValueError("A2A exposure is a boolean available only for bearer webhooks")
            result["a2a_enabled"] = a2a
            mcp = data.get("mcp_enabled", False)
            if type(mcp) is not bool or (mcp and data.get("type", "webhook") != "webhook"):
                raise ValueError("MCP exposure is a boolean available only for bearer webhooks")
            result["mcp_enabled"] = mcp
            if data.get("type", "webhook") != "webhook":
                from nexus.core.studio_messaging import validate_channel

                result.update(validate_channel(data))
            return result
        if kind == "connectors":
            from nexus.core.studio_connections import NATIVE, validate_connection

            if data.get("type") in NATIVE:
                return validate_connection(data)
            connector_type = data.get("type")
            enabled = data.get("enabled", False)
            if type(enabled) is not bool:
                raise ValueError("Enabled must be true or false")
            if connector_type == "http":
                endpoint = _text(data.get("url"), "Endpoint URL", 2000)
                parsed = urlparse(endpoint)
                if (
                    parsed.scheme not in {"https", "http"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.fragment
                ):
                    raise ValueError("Use an HTTP(S) URL without credentials or fragment")
                env = data.get("token_env", "")
                if (
                    not isinstance(env, str)
                    or env
                    and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", env)
                ):
                    raise ValueError("Use an environment variable name for the token")
                private = data.get("allow_private", False)
                if type(private) is not bool:
                    raise ValueError("Allow private networks must be true or false")
                return {
                    "type": "http",
                    "url": endpoint,
                    "token_env": env,
                    "enabled": enabled,
                    "allow_private": private,
                }
            if connector_type == "sqlite":
                relative = _text(data.get("path"), "Database path", 500)
                path = self.studio.root / relative
                if (
                    path.is_absolute()
                    and relative.startswith("/")
                    or not path.resolve().is_relative_to(self.studio.root)
                ):
                    raise ValueError("Database path must stay inside the workspace")
                return {"type": "sqlite", "path": relative, "enabled": enabled}
            raise ValueError("Supported local connector types are HTTP GET and read-only SQLite")
        raise ValueError("Unknown operation collection")

    def launch_target(self, data, task, requester=None, context=None):
        return self.studio.start_run(
            {
                **(context or {}),
                "kind": "chat" if data["target_kind"] == "agent" else "workflow",
                "agent_id" if data["target_kind"] == "agent" else "workflow_id": data["target_id"],
                "task": _text(task, "Task", 16000),
                "requester_nexus_id": requester,
            }
        )

    def trigger_entrypoint(self, identity, payload):
        record = self.studio.catalog.get("entrypoints", identity)
        if not record["data"]["enabled"]:
            raise ValueError("Entrypoint is disabled")
        self.studio.resource_lifecycle.assert_launch("entrypoints:" + identity)
        return self.launch_target(
            record["data"],
            payload.get("task"),
            payload.get("requester_nexus_id") or record.get("owner_id"),
            {key: payload[key] for key in ("artifact_ids", "workflow_input") if key in payload},
        )

    def authorize_entrypoint(self, identity, bearer):
        import secrets

        data = self.studio.catalog.get("entrypoints", identity)["data"]
        secret = os.getenv(data["token_env"], "")
        allowed = bool(
            data.get("type", "webhook") == "webhook"
            and data["enabled"]
            and len(secret) >= 24
            and secrets.compare_digest(bearer, secret)
        )
        if allowed:
            try:
                self.studio.resource_lifecycle.assert_launch("entrypoints:" + identity)
            except (ValueError, FileNotFoundError):
                return False
        return allowed

    def reconcile_connectors(self):
        import hashlib

        from nexus.tools import tool_registry

        prefix = "connector:" + hashlib.sha256(str(self.studio.root).encode()).hexdigest()[:8] + ":"
        for name, func in self.tools.items():
            tool_registry.unregister(name, func=func)
        self.tools = {}
        for item in self.studio.catalog.list("connectors"):
            if not item["data"]["enabled"]:
                continue
            identity = item["id"]

            call = self.connector_tool(identity)

            name = prefix + identity
            tool_registry.register(
                name, call, description=item["description"] or item["name"], category="connector"
            )
            self.tools[name] = call

    def connector_tool(self, identity):
        from nexus.core.studio_connections import NATIVE, execution_identity

        if self.studio.catalog.get("connectors", identity)["data"]["type"] in NATIVE:
            return self.studio.connections.tool(identity)

        async def invoke(query=""):
            return await asyncio.to_thread(
                self.call_connector, identity, {"query": query}, execution_identity.get()
            )

        return invoke

    def call_connector(self, identity, payload, actor=None, *, probe=False, expected_data=None):
        from nexus.core.studio_connections import NATIVE

        data = self.studio.catalog.get("connectors", identity)["data"]
        if expected_data is not None and data != expected_data:
            raise ValueError("Connection changed before its check")
        if not data["enabled"] and not probe:
            raise ValueError("Connector is disabled")
        if not probe:
            self.studio.resource_lifecycle.assert_launch("connectors:" + identity)
        if data["type"] in NATIVE:
            result = asyncio.run(
                self.studio.connections.execute(
                    identity, payload, actor, probe=probe, expected_data=expected_data
                )
            )
            return json.dumps(result, ensure_ascii=False, default=str)
        if data["type"] == "sqlite":
            query = _text(
                payload.get("query") or "SELECT name FROM sqlite_master WHERE type='table'",
                "Read-only query",
                16000,
            )
            path = (self.studio.root / data["path"]).resolve()
            if not path.is_relative_to(self.studio.root) or not path.is_file():
                raise ValueError("Database file must exist inside the workspace")
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as connection:
                allowed = {
                    sqlite3.SQLITE_SELECT,
                    sqlite3.SQLITE_READ,
                    sqlite3.SQLITE_FUNCTION,
                    sqlite3.SQLITE_RECURSIVE,
                }
                connection.set_authorizer(
                    lambda action, a, b, db, source: (
                        sqlite3.SQLITE_OK
                        if action in allowed and str(b).lower() != "load_extension"
                        else sqlite3.SQLITE_DENY
                    )
                )
                deadline = time.monotonic() + 3
                connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                cursor = connection.execute(query)
                rows = cursor.fetchmany(101)
                result = {
                    "columns": [column[0] for column in cursor.description or []],
                    "rows": rows[:100],
                    "truncated": len(rows) > 100,
                }
            output = json.dumps(result, ensure_ascii=False, default=str)
            if len(output.encode()) > 262144:
                raise ValueError("Query result exceeds 256 KiB; narrow the query")
            return output
        parsed = urlparse(data["url"])
        if not data["allow_private"]:
            addresses = socket.getaddrinfo(
                parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
            )
            if parsed.scheme != "https" or any(
                not ipaddress.ip_address(address[4][0]).is_global for address in addresses
            ):
                raise ValueError("Private/local HTTP requires explicitly allowing private networks")
        headers = {"Accept": "application/json, text/plain", "User-Agent": "NexusStudio/1"}
        if data["token_env"]:
            token = os.getenv(data["token_env"])
            if not token:
                raise ValueError("Connector token environment variable is not configured")
            headers["Authorization"] = "Bearer " + token

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("Connector redirects are disabled; configure the final URL")

        request = urllib.request.Request(data["url"], headers=headers, method="GET")
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
                content = response.read(262145)
                if len(content) > 262144:
                    raise ValueError("Connector response exceeds 256 KiB")
                return content.decode("utf-8", errors="replace")
        except OSError as exc:
            # Exceptions may embed credential-bearing server messages; keep them out of UI/logs.
            raise ValueError(f"Connector request failed ({type(exc).__name__})") from None

    def evaluation_request(self, payload):
        suite = self.studio.catalog.get("evaluations", payload.get("evaluation_id"))
        variants = payload.get("variants", [{"name": "Saved configuration"}])
        if not isinstance(variants, list) or not 1 <= len(variants) <= 4:
            raise ValueError("An experiment supports 1 to 4 variants")
        from nexus.core.studio import _PROVIDERS

        for variant in variants:
            if not isinstance(variant, dict) or set(variant) - {"name", "provider", "model"}:
                raise ValueError("Invalid experiment variant")
            _text(variant.get("name"), "Variant name", 100)
            if variant.get("provider") and variant["provider"] not in {p[0] for p in _PROVIDERS}:
                raise ValueError("Unknown variant provider")
            if variant.get("model") is not None and not isinstance(variant["model"], str):
                raise ValueError("Variant model must be text")
        payload["suite_snapshot"] = suite
        payload["variants"] = variants

    async def evaluate(self, run_id, payload):
        suite = payload["suite_snapshot"]
        target = suite["data"]
        report = {
            "id": run_id,
            "suite_id": suite["id"],
            "owner_id": payload.get("requester_nexus_id"),
            "suite_revision": suite["revision"],
            "name": suite["name"],
            "created_at": datetime.now(UTC).isoformat(),
            "results": [],
            "variants": payload["variants"],
        }
        for variant_index, variant in enumerate(payload["variants"]):
            for index, case in enumerate(target["cases"]):
                started = time.monotonic()
                self.studio._update_run(run_id, step=f"{variant['name']} · case {index + 1}")
                entry = {"variant": variant["name"], "case": index + 1, "input": case["input"]}
                request = {key: variant[key] for key in ("provider", "model") if variant.get(key)}
                request["requester_nexus_id"] = payload.get("requester_nexus_id")
                try:
                    if target["target_kind"] == "workflow":
                        output = await self.studio._workflow(
                            run_id,
                            {
                                **request,
                                "workflow_id": target["target_id"],
                                "task": case["input"],
                                "_evaluation_case": f"{run_id}-{variant_index}-{index}",
                            },
                        )
                    else:
                        result = await self.studio._invoke(
                            {**request, "agent_id": target["target_id"]}, case["input"], run_id
                        )
                        output = result.output
                    lowered = output.casefold()
                    checks = [
                        {
                            "type": "contains",
                            "phrase": phrase,
                            "passed": phrase.casefold() in lowered,
                        }
                        for phrase in case["contains"]
                    ] + [
                        {
                            "type": "excludes",
                            "phrase": phrase,
                            "passed": phrase.casefold() not in lowered,
                        }
                        for phrase in case["excludes"]
                    ]
                    from nexus.core.studio_evaluations import additional_checks

                    checks.extend(
                        await additional_checks(
                            self.studio, case, output, run_id, payload.get("requester_nexus_id")
                        )
                    )
                    entry.update(
                        output=output,
                        checks=checks,
                        passed=all(check["passed"] for check in checks),
                    )
                except Exception as exc:
                    entry.update(output="", checks=[], passed=False, error=str(exc))
                entry["duration_seconds"] = round(time.monotonic() - started, 3)
                report["results"].append(entry)
                report.update(
                    updated_at=datetime.now(UTC).isoformat(),
                    passed=sum(r["passed"] for r in report["results"]),
                    total=len(report["results"]),
                )
                self.studio._write(self.studio._path("evaluation-reports", run_id), report)
                if entry.get("error"):
                    self.studio._update_run(run_id, evaluation_report_id=run_id)
                    raise RuntimeError(
                        "Evaluation stopped after provider/runtime error: " + entry["error"]
                    )
        scores = [
            check["score"]
            for entry in report["results"]
            for check in entry["checks"]
            if "score" in check
        ]
        report["judged_cases"] = len(scores)
        if scores:
            report["mean_judge_score"] = round(sum(scores) / len(scores), 4)
        self.studio._write(self.studio._path("evaluation-reports", run_id), report)
        self.studio._update_run(run_id, evaluation_report_id=run_id)
        return json.dumps(report, ensure_ascii=False, indent=2)

    def tick(self, now=None):
        from nexus.core.studio_schedules import StudioSchedules

        return StudioSchedules(self.studio).tick(now)

    def start(self):
        if self.worker is not None:
            return

        def loop():
            while not self.stop_event.wait(1):
                try:
                    self.tick()
                    self.studio.messaging.tick()
                except Exception as exc:
                    self.studio._write(
                        self.studio._path("schedule-state", "scheduler"),
                        {
                            "id": "scheduler",
                            "last_error": str(exc),
                            "updated_at": datetime.now(UTC).isoformat(),
                        },
                    )

        self.worker = threading.Thread(target=loop, daemon=True, name="nexus-schedules")
        self.worker.start()

    def close(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=2)
        from nexus.tools import tool_registry

        for name, func in self.tools.items():
            tool_registry.unregister(name, func=func)
