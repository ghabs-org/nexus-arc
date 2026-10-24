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
            from nexus.adapters.ai.base import EFFORT_LEVELS
            from nexus.core.studio import _PROVIDERS

            provider = data.get("provider")
            if provider not in {p[0] for p in _PROVIDERS}:
                raise ValueError("Unknown model profile provider")
            model_spec = {"provider": provider, "model": _text(data.get("model"), "Model ID", 200)}
            if data.get("effort"):
                effort = str(data["effort"]).strip().lower()
                if effort not in EFFORT_LEVELS:
                    raise ValueError(f"Effort must be one of {', '.join(EFFORT_LEVELS)}")
                model_spec["effort"] = effort
            return model_spec
        if kind == "decisionmodels":
            from nexus.core.studio_decisions import finite, validate_binding

            backend = str(data.get("backend", "")).strip()
            model = str(data.get("model", "")).strip()
            validate_binding({"backend": backend, "model": model})
            timeout = data.get("timeout_seconds", 10)
            if isinstance(timeout, str):
                try:
                    timeout = float(timeout)
                except (ValueError, TypeError):
                    raise ValueError("Decision call timeout must be 1–300 seconds")
            if not finite(timeout, 1, 300):
                raise ValueError("Decision call timeout must be 1–300 seconds")
            return {
                "backend": backend,
                "model": model,
                "timeout_seconds": int(timeout)
                if isinstance(timeout, int) or (isinstance(timeout, float) and timeout.is_integer())
                else float(timeout),
            }
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
            task = data.get("task")
            if target["target_kind"] == "workflow" and not (isinstance(task, str) and task.strip()):
                doc = self.studio.definition("workflows", target["target_id"])
                if isinstance(doc, dict):
                    doc = doc.get("document", doc)
                task = (
                    doc.get("metadata", {}).get("description")
                    or doc.get("metadata", {}).get("name")
                    or f"Run workflow {target['target_id']}"
                )
            else:
                task = _text(task, "Task", 16000)
            schedule_spec = {
                **target,
                **calendar,
                "enabled": enabled,
                "task": task,
                "run_at": _timestamp(initial).isoformat(),
                "interval_seconds": interval,
            }
            if "workflow_input" in data and data["workflow_input"] is not None:
                schedule_spec["workflow_input"] = data["workflow_input"]
            return schedule_spec
        if kind == "entrypoints":
            target = self.target(data, targets)
            auth_mode = data.get("auth_mode", data.get("authMode", "bearer"))
            if auth_mode not in {"bearer", "hmac"}:
                raise ValueError("Auth mode must be 'bearer' or 'hmac'")
            env_val = data.get("token_env") or data.get("hmac_secret_env") or data.get("hmacSecret")
            if isinstance(env_val, str) and env_val.startswith("${") and env_val.endswith("}"):
                env_val = env_val[2:-1]
            env = _text(env_val, "Token or secret environment variable", 96)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env):
                raise ValueError("Use an environment variable name, not the secret value")
            enabled = data.get("enabled", data.get("deploy", False))
            if type(enabled) is not bool:
                raise ValueError("Enabled must be true or false")
            result = {
                **target,
                "token_env": env,
                "enabled": enabled,
                "auth_mode": auth_mode,
            }
            if auth_mode == "hmac":
                slug = data.get("slug")
                if slug is not None:
                    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-_]{1,64}", slug):
                        raise ValueError(
                            "Slug must be 1-64 characters of lowercase alphanumeric, hyphens, or underscores"
                        )
                    result["slug"] = slug
                route_path = data.get("route_path", data.get("routePath", ""))
                if route_path:
                    if not isinstance(route_path, str) or not route_path.startswith("/"):
                        raise ValueError("Route path must start with '/'")
                    result["route_path"] = route_path
                hmac_header = data.get("hmac_header", data.get("hmacHeader", "X-Hub-Signature-256"))
                if not isinstance(hmac_header, str) or not hmac_header.strip():
                    raise ValueError("HMAC signature header must be a nonempty string")
                result["hmac_header"] = hmac_header.strip()
                algorithm = data.get("hmac_algorithm", data.get("hmacAlgorithm", "sha256")).lower()
                if algorithm not in {"sha256", "sha1", "sha512"}:
                    raise ValueError("Supported HMAC algorithms are sha256, sha1, and sha512")
                result["hmac_algorithm"] = algorithm
                result["hmac_prefix"] = data.get(
                    "hmac_prefix",
                    data.get("hmacPrefix", f"{algorithm}=" if algorithm == "sha256" else ""),
                )
                encoding = data.get("hmac_encoding", data.get("hmacEncoding", "hex")).lower()
                if encoding not in {"hex", "base64"}:
                    raise ValueError("Supported HMAC encodings are hex and base64")
                result["hmac_encoding"] = encoding
                if "input_expression" in data or "inputExpression" in data:
                    result["input_expression"] = str(
                        data.get("input_expression", data.get("inputExpression"))
                    )
            a2a = data.get("a2a_enabled", False)
            if type(a2a) is not bool or (
                a2a and (data.get("type", "webhook") != "webhook" or auth_mode != "bearer")
            ):
                raise ValueError("A2A exposure is a boolean available only for bearer webhooks")
            result["a2a_enabled"] = a2a
            if "a2a_push" in data:
                from nexus.core.studio_a2a_push import settings

                if not a2a:
                    raise ValueError("Enable A2A before configuring callback destinations")
                result["a2a_push"] = settings(data["a2a_push"])
            mcp = data.get("mcp_enabled", False)
            if type(mcp) is not bool or (
                mcp and (data.get("type", "webhook") != "webhook" or auth_mode != "bearer")
            ):
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
        effective_task = task or data.get("task")
        if data.get("target_kind") == "workflow" and not (
            isinstance(effective_task, str) and effective_task.strip()
        ):
            doc = self.studio.definition("workflows", data["target_id"])
            if isinstance(doc, dict):
                doc = doc.get("document", doc)
            effective_task = (
                doc.get("metadata", {}).get("description")
                or doc.get("metadata", {}).get("name")
                or f"Run workflow {data['target_id']}"
            )
        extra = {}
        if "workflow_input" in data and data["workflow_input"] is not None:
            extra["workflow_input"] = data["workflow_input"]
        return self.studio.start_run(
            {
                **(context or {}),
                "kind": "chat" if data["target_kind"] == "agent" else "workflow",
                "agent_id" if data["target_kind"] == "agent" else "workflow_id": data["target_id"],
                "task": _text(effective_task, "Task", 16000),
                "requester_nexus_id": requester,
                **extra,
            }
        )

    def find_entrypoint(self, identifier, path_suffix=""):
        entrypoints = self.studio.catalog.list("entrypoints")
        for ep in entrypoints:
            data = ep.get("data", {})
            if ep["id"] == identifier or data.get("slug") == identifier:
                expected_route = data.get("route_path", "")
                if expected_route:
                    norm_expected = expected_route.strip("/")
                    norm_suffix = path_suffix.strip("/")
                    if norm_expected != norm_suffix:
                        continue
                return ep
        return None

    def trigger_entrypoint(self, identity, payload):
        record = self.studio.catalog.get("entrypoints", identity)
        if not record["data"]["enabled"]:
            raise ValueError("Entrypoint is disabled")
        self.studio.resource_lifecycle.assert_launch("entrypoints:" + identity)
        context = {
            key: payload[key] for key in ("artifact_ids", "workflow_input") if key in payload
        }
        if "workflow_input" not in context and isinstance(payload, dict):
            context["workflow_input"] = {
                **payload,
                "text": payload["text"]
                if "text" in payload
                else json.dumps(payload, ensure_ascii=False),
                "payload": payload,
            }
        task = payload.get("task")
        if not task:
            if "text" in payload and isinstance(payload["text"], str) and payload["text"].strip():
                task = payload["text"]
            elif isinstance(payload, dict) and payload:
                task = json.dumps(payload, ensure_ascii=False)
        return self.launch_target(
            record["data"],
            task,
            payload.get("requester_nexus_id") or record.get("owner_id"),
            context,
        )

    def authorize_entrypoint(self, identity, bearer="", headers=None, raw_body=None):
        import base64
        import hashlib
        import hmac
        import secrets

        data = self.studio.catalog.get("entrypoints", identity)["data"]
        secret = os.getenv(data["token_env"], "")
        if not data.get("enabled"):
            return False
        auth_mode = data.get("auth_mode", "bearer")
        if auth_mode == "hmac":
            if not secret or len(secret) < 16 or headers is None or raw_body is None:
                return False
            header_name = data.get("hmac_header", "X-Hub-Signature-256").lower()
            normalized_headers = {k.lower(): v for k, v in headers.items()}
            sig_header = normalized_headers.get(header_name, "")
            if not sig_header:
                return False
            prefix = data.get("hmac_prefix", "")
            if prefix and sig_header.startswith(prefix):
                sig_header = sig_header[len(prefix) :]
            algo = {
                "sha256": hashlib.sha256,
                "sha1": hashlib.sha1,
                "sha512": hashlib.sha512,
            }.get(data.get("hmac_algorithm", "sha256"), hashlib.sha256)
            digest = hmac.new(secret.encode(), raw_body, algo)
            encoding = data.get("hmac_encoding", "hex")
            expected_sig = (
                base64.b64encode(digest.digest()).decode()
                if encoding == "base64"
                else digest.hexdigest()
            )
            allowed = secrets.compare_digest(sig_header, expected_sig)
        else:
            allowed = bool(
                data.get("type", "webhook") == "webhook"
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
            description = item["description"] or item["name"]
            if item["data"]["type"] == "nuclia" and item["data"].get("template"):
                parameters = item["data"]["template"].get("parameters", [])
                schema = [
                    {
                        key: parameter[key]
                        for key in ("name", "type", "required", "nullable", "description")
                        if key in parameter
                    }
                    for parameter in parameters
                    if "context_path" not in parameter
                ]
                description += (
                    "\nNuclia selected actions: "
                    + ", ".join(item["data"]["actions"])
                    + ". Pass runtime values in arguments.parameters. Declared parameters: "
                    + json.dumps(schema, ensure_ascii=False)
                    + ". Configured defaults apply server-side; runtime filters cannot replace a stored filter."
                )
            tool_registry.register(name, call, description=description, category="connector")
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
            from nexus.core.studio_remote import remote_headers

            headers.update(remote_headers(self.studio, data, actor))

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
        from nexus.core.studio_experiments import prepare

        prepare(self.studio, payload)

    async def evaluate(self, run_id, payload):
        from copy import deepcopy

        from nexus.core.studio_graph import StudioPausedError

        progress_path = self.studio._path("evaluation-progress", run_id)
        report_path = self.studio._path("evaluation-reports", run_id)
        resumed = progress_path.exists()
        resume_mode = next(
            (key for key in ("resume_graph", "resume_yaml") if payload.get(key)), None
        )
        progress = (
            self.studio._read(progress_path)
            if resumed
            else {
                "id": run_id,
                "version": 1,
                "payload": deepcopy(payload),
                "checkpoint_id": None,
            }
        )
        payload = progress["payload"]
        suite = payload["suite_snapshot"]
        target = suite["data"]
        from nexus.core.studio_experiments import summarize

        settings = payload.get(
            "_experiment_settings", {"repeats": 1, "threshold": 1, "enforce": False}
        )
        repeats = settings["repeats"]
        report = (
            self.studio._read(report_path)
            if resumed
            else {
                "id": run_id,
                "suite_id": suite["id"],
                "owner_id": payload.get("requester_nexus_id"),
                "suite_revision": suite["revision"],
                "name": suite["name"],
                "created_at": datetime.now(UTC).isoformat(),
                "results": [],
                "total": 0,
                "passed": 0,
                "variants": payload["variants"],
                "runs_per_example": repeats,
                "pass_threshold": settings["threshold"],
                "expected_total": len(target["cases"]) * len(payload["variants"]) * repeats,
                "complete": False,
                "acceptance": None,
                "evaluation_status": "running",
            }
        )
        if payload.get("experiment_snapshot"):
            experiment = payload["experiment_snapshot"]
            report.update(
                experiment_id=experiment["id"],
                experiment_revision=experiment["revision"],
                experiment_name=experiment["name"],
            )
        report["evaluation_status"] = "running"
        self.studio._write(report_path, report)
        self.studio._update_run(run_id, evaluation_report_id=run_id)
        for variant_index, variant in enumerate(payload["variants"]):
            for index, repetition, case in (
                (index, repetition, case)
                for index, case in enumerate(target["cases"])
                for repetition in range(repeats)
            ):
                ordinal = (variant_index * len(target["cases"]) + index) * repeats + repetition
                if ordinal < len(report["results"]):
                    continue
                checkpoint = f"{run_id}-{variant_index}-{index}" + (
                    f"-repeat-{repetition}" if repeats > 1 else ""
                )
                active_resume = resume_mode if progress["checkpoint_id"] == checkpoint else None
                progress["checkpoint_id"] = checkpoint
                self.studio._write(progress_path, progress)
                started = time.monotonic()
                self.studio._update_run(
                    run_id,
                    step=f"{variant['name']} · case {index + 1} · repetition {repetition + 1}/{repeats}",
                    evaluation_case={
                        "variant": variant["name"],
                        "case": index + 1,
                        "repetition": repetition + 1,
                        "runs_per_example": repeats,
                    },
                )
                self.studio.execution.record(
                    run_id,
                    "evaluation-case",
                    node_id=checkpoint,
                    node_name=f"{variant['name']} · case {index + 1} · repetition {repetition + 1}/{repeats}",
                    status="continued" if active_resume else "running",
                )
                entry = {
                    "variant": variant["name"],
                    "variant_index": variant_index + 1,
                    "case": index + 1,
                    "repetition": repetition + 1,
                    "input": case["input"],
                }
                request = {
                    key: variant[key] for key in ("provider", "model", "effort") if variant.get(key)
                }
                request["requester_nexus_id"] = payload.get("requester_nexus_id")
                request.update(
                    {
                        key: deepcopy(payload[key])
                        for key in (
                            "agent_snapshots",
                            "workflow_snapshot",
                            "workflow_snapshots",
                            "_workflow_limits",
                            "_media_profile_revisions",
                        )
                        if key in payload
                    }
                )
                if active_resume:
                    request[active_resume] = True
                # A continuation authorizes only this case, never a later repetition.
                resume_mode = None
                try:
                    if target["target_kind"] == "workflow":
                        output = await self.studio._workflow(
                            run_id,
                            {
                                **request,
                                "workflow_id": target["target_id"],
                                "task": case["input"],
                                "_evaluation_case": checkpoint,
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
                except StudioPausedError:
                    import uuid

                    report.update(
                        evaluation_status="waiting",
                        updated_at=datetime.now(UTC).isoformat(),
                    )
                    summarize(report)
                    self.studio._write(report_path, report)
                    self.studio._update_run(run_id, evaluation_checkpoint=uuid.uuid4().hex)
                    raise
                except Exception as exc:
                    entry.update(output="", checks=[], passed=False, error=str(exc))
                entry["duration_seconds"] = round(time.monotonic() - started, 3)
                report["results"].append(entry)
                report.update(
                    updated_at=datetime.now(UTC).isoformat(),
                    passed=sum(r["passed"] for r in report["results"]),
                    total=len(report["results"]),
                )
                summarize(report)
                if entry.get("error"):
                    report["evaluation_status"] = "error"
                self.studio._write(self.studio._path("evaluation-reports", run_id), report)
                self.studio.execution.record(
                    run_id,
                    "evaluation-case",
                    node_id=checkpoint,
                    status="failed" if entry.get("error") else "completed",
                )
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
        summarize(report, complete=True)
        report["judged_cases"] = len(scores)
        if scores:
            report["mean_judge_score"] = round(sum(scores) / len(scores), 4)
        self.studio._write(self.studio._path("evaluation-reports", run_id), report)
        progress["complete"] = True
        self.studio._write(progress_path, progress)
        self.studio._update_run(run_id, evaluation_report_id=run_id)
        if settings["enforce"] and not report["acceptance"]:
            raise RuntimeError(
                "Experiment pass threshold not met by every variant; inspect the completed report"
            )
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
                    self.studio.a2a.push.tick()
                    self.studio.external.callbacks.tick()
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
