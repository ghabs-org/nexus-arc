"""Local HTTP surface for Nexus Studio; no frontend build or API-key storage."""

from __future__ import annotations

import json
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from nexus.core.studio import StudioService
from nexus.core.studio_desktop import (
    DesktopLaunchCredential,
    WorkspaceInUseError,
    WorkspaceLock,
    check_workspace_root,
    end_owned_process_group,
)
from nexus.core.studio_team import TeamAccessError
from nexus.core.studio_termination import serve_until_stopped, termination_requested

_ASSETS = {
    "/agent.html": ("index.html", "text/html; charset=utf-8"),
    "/artifact.html": ("artifact.html", "text/html; charset=utf-8"),
    "/artifact-viewer.js": ("artifact-viewer.js", "text/javascript; charset=utf-8"),
    "/shared.html": ("shared.html", "text/html; charset=utf-8"),
    "/shared.js": ("shared.js", "text/javascript; charset=utf-8"),
    "/vendor/marked.js": ("vendor/marked.js", "text/javascript; charset=utf-8"),
    "/vendor/purify.js": ("vendor/purify.js", "text/javascript; charset=utf-8"),
    "/content.js": ("content.js", "text/javascript; charset=utf-8"),
    "/conversation-content.js": ("conversation-content.js", "text/javascript; charset=utf-8"),
    "/features.js": ("features.js", "text/javascript; charset=utf-8"),
    "/workspace-access.js": ("workspace-access.js", "text/javascript; charset=utf-8"),
    "/experience.js": ("experience.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/service-worker.js": ("service-worker.js", "text/javascript; charset=utf-8"),
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/studio.css": ("studio.css", "text/css; charset=utf-8"),
    "/workbench.js": ("workbench.js", "text/javascript; charset=utf-8"),
    "/integrations.js": ("integrations.js", "text/javascript; charset=utf-8"),
    "/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
    "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
    "/operations.js": ("operations.js", "text/javascript; charset=utf-8"),
    "/library.js": ("library.js", "text/javascript; charset=utf-8"),
    "/quick-build.js": ("quick-build.js", "text/javascript; charset=utf-8"),
    "/studio.js": ("studio.js", "text/javascript; charset=utf-8"),
}
_MAX_BODY = 1024 * 1024
_DESKTOP_SESSION_REQUIRED = {"error": "Open this workspace from Nexus Studio Desktop"}

# Vendored diagram modules are an explicit local inventory, never a generic
# filesystem route. URL traversal cannot reach a file outside this allowlist.
for _module in (Path(__file__).parent / "studio_static" / "vendor" / "mermaid").rglob("*.mjs"):
    _relative = _module.relative_to(Path(__file__).parent / "studio_static").as_posix()
    _ASSETS["/" + _relative] = (_relative, "text/javascript; charset=utf-8")


def make_studio_handler(
    service: StudioService, token: str, launch: DesktopLaunchCredential | None = None
):
    """Bind the reusable service to a loopback-only browser API.

    With a native ``launch`` credential, ``/api/`` routes without their own
    authentication also require the shell-issued desktop session cookie.
    """

    class StudioHandler(BaseHTTPRequestHandler):
        server_version = "NexusStudio/1"

        def log_message(self, *args):
            pass

        def send_payload(self, status, data, *, headers=None):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_bytes(status, body, "application/json; charset=utf-8", headers=headers)

        def send_bytes(self, status, body, content_type, *, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; media-src 'self' data: blob:; frame-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors "
                + (
                    (
                        "'self'"
                        + (
                            " " + " ".join(service.team.embed_origins)
                            if service.team and service.team.embed_origins
                            else ""
                        )
                    )
                    if urlparse(self.path).path == "/agent.html"
                    else "'none'"
                )
                + "; base-uri 'none'; object-src 'none'",
            )
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            if service.team:
                return self.headers.get("Host") == service.team.host
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def desktop_session(self):
            return launch is None or launch.session_ok(self.headers.get("Cookie"))

        def visible_items(self, records):
            if not service.team:
                return records
            return [record for record in records if service.team.visible(record, self.actor)]

        def private_record(self, record):
            if service.team and not service.team.visible(record, self.actor):
                raise TeamAccessError("Resource is private to another user")
            return record

        def handle_a2a_rest(self, route):
            from nexus.core.studio_a2a_http import error_response, request_operation
            from nexus.core.studio_a2a_server import A2AError

            def reply(status, value):
                return self.send_bytes(
                    status,
                    json.dumps(value, ensure_ascii=False).encode(),
                    "application/a2a+json; charset=utf-8",
                )

            try:
                parts = route.strip("/").split("/")
                if len(parts) < 5 or parts[3] != "rest":
                    raise FileNotFoundError
                bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
                record = service.a2a.authorize(parts[2], bearer)
                origin = (
                    service.team.public_url if service.team else "http://" + self.headers["Host"]
                )
                if self.headers.get("Origin") not in {None, origin}:
                    raise PermissionError
                version = service.a2a.version(self.headers.get("A2A-Version") or "1.0")
                if version != "1.0":
                    raise A2AError(-32009, "HTTP+JSON supports A2A 1.0; use JSON-RPC for 0.3")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 24 * 1024 * 1024:
                    return reply(
                        *error_response(status=413, message="A2A request must be at most 24 MiB")
                    )
                body = {}
                if length:
                    if self.command == "GET":
                        raise A2AError(-32602, "GET request bodies are not supported")
                    if self.headers.get_content_type() not in {
                        "application/json",
                        "application/a2a+json",
                    }:
                        return reply(*error_response(status=415, message="Expected JSON"))
                    try:
                        body = json.loads(self.rfile.read(length))
                    except (ValueError, UnicodeDecodeError):
                        raise A2AError(-32700, "Invalid JSON") from None
                operation, params = request_operation(
                    self.command,
                    "/".join(parts[4:]),
                    parse_qs(urlparse(self.path).query, keep_blank_values=True),
                    body,
                )
                result, streaming = service.a2a.invoke(record, operation, params, version, origin)
                service.a2a.authorize(parts[2], bearer)
                if not streaming:
                    return reply(200, result)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    for event in service.a2a.updates(record, result, version, bearer):
                        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.flush()
                except (PermissionError, ValueError, OSError):
                    pass  # Reconnect to this task ID; never repeat the send.
            except PermissionError:
                reply(*error_response(status=403, message="A2A entrypoint authorization required"))
            except FileNotFoundError:
                reply(*error_response(status=404, message="A2A entrypoint not found"))
            except (ValueError, TypeError, KeyError) as error:
                if isinstance(error, A2AError) and error.code == -32601:
                    reply(*error_response(status=404, message="Operation not found"))
                else:
                    reply(*error_response(error))

        def handle_a2a(self, route, *, card=False):
            from nexus.core.studio_a2a_server import A2AError

            request_id = None
            try:
                parts = route.strip("/").split("/")
                if len(parts) != (4 if card else 3) or (card and parts[-1] != "agent-card.json"):
                    raise FileNotFoundError
                bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
                record = service.a2a.authorize(parts[2], bearer)
                origin = (
                    service.team.public_url if service.team else "http://" + self.headers["Host"]
                )
                if self.headers.get("Origin") not in {None, origin}:
                    raise PermissionError("Cross-origin A2A requests are not supported")
                version = service.a2a.version(self.headers.get("A2A-Version"))
                if card:
                    return self.send_payload(200, service.a2a.card(record, origin, version))
                if self.headers.get_content_type() not in {
                    "application/json",
                    "application/a2a+json",
                }:
                    return self.send_payload(415, {"error": "Expected JSON"})
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 24 * 1024 * 1024:
                    return self.send_payload(413, {"error": "A2A request must be at most 24 MiB"})
                try:
                    request = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeDecodeError):
                    raise A2AError(-32700, "Invalid JSON") from None
                if isinstance(request, dict) and type(request.get("id")) in {str, int}:
                    request_id = request["id"]
                result, streaming = service.a2a.dispatch(record, request, version, origin)
                service.a2a.authorize(parts[2], bearer)
                if not streaming:
                    return self.send_payload(
                        200, {"jsonrpc": "2.0", "id": request_id, "result": result}
                    )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    for event in service.a2a.updates(record, result, version, bearer):
                        self.wfile.write(
                            (
                                "data: "
                                + json.dumps({"jsonrpc": "2.0", "id": request_id, "result": event})
                                + "\n\n"
                            ).encode()
                        )
                        self.wfile.flush()
                except (PermissionError, ValueError, OSError):
                    pass  # Client recovers by task ID; never create another task here.
            except PermissionError:
                self.send_payload(403, {"error": "A2A entrypoint authorization required"})
            except FileNotFoundError:
                self.send_payload(404, {"error": "A2A entrypoint not found"})
            except (ValueError, TypeError, KeyError) as exc:
                self.send_payload(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": exc.code if isinstance(exc, A2AError) else -32602,
                            "message": str(exc)
                            if isinstance(exc, ValueError)
                            else "Invalid parameters",
                        },
                    },
                )

        def do_GET(self):
            if not self.trusted_host():
                return self.send_payload(403, {"error": "Unrecognized local host"})
            route = urlparse(self.path).path
            if launch and route == "/api/desktop/handshake":
                try:
                    proof = launch.proof(
                        self.headers.get("X-Nexus-Desktop-Challenge", ""),
                        str(service.root),
                        self.server.server_port,
                    )
                except ValueError:
                    return self.send_payload(400, {"error": "Invalid desktop challenge"})
                return self.send_payload(200, {"proof": proof})
            self.actor = service.team.principal(self.headers) if service.team else None
            if route.startswith("/api/a2a/"):
                if route.strip("/").split("/")[3:4] == ["rest"]:
                    return self.handle_a2a_rest(route)
                return self.handle_a2a(route, card=True)
            if route.startswith("/api/mcp/"):
                return self.handle_mcp(route)
            try:
                if route.startswith("/api/public-shares/"):
                    share_parts = route.strip("/").split("/")
                    if len(share_parts) == 5 and share_parts[3] == "artifacts":
                        return self.send_payload(
                            200,
                            service.collaboration.shared_artifact(
                                share_parts[2], share_parts[4], public=True
                            ),
                        )
                    if len(share_parts) != 3:
                        raise FileNotFoundError("Shared snapshot not found")
                    return self.send_payload(
                        200, service.collaboration.get_share(share_parts[2], public=True)
                    )
                if route == "/api/auth/status":
                    return self.send_payload(
                        200,
                        {
                            "mode": "team" if service.team else "desktop",
                            "authenticated": bool(self.actor)
                            if service.team
                            else self.desktop_session(),
                            "login_url": service.team.login_url if service.team else None,
                        },
                    )
                if route.startswith("/api/") and service.team and not self.actor:
                    return self.send_payload(
                        401,
                        {
                            "error": "Nexus sign-in and project access required",
                            "login_url": service.team.login_url,
                        },
                    )
                if route.startswith("/api/") and not self.desktop_session():
                    return self.send_payload(401, _DESKTOP_SESSION_REQUIRED)
                if route == "/api/studio/oauth/callback":
                    return self.send_payload(
                        200,
                        service.oauth.callback(
                            parse_qs(urlparse(self.path).query, keep_blank_values=True),
                            self.headers.get("Cookie"),
                            self.actor,
                        ),
                    )
                if route in _ASSETS:
                    name, mime = _ASSETS[route]
                    return self.send_bytes(
                        200, (Path(__file__).parent / "studio_static" / name).read_bytes(), mime
                    )
                if route == "/api/bootstrap":
                    return self.send_payload(
                        200,
                        {
                            "account_url": service.team.login_url if service.team else None,
                            "token": service.team.csrf(token, self.actor)
                            if service.team
                            else token,
                            "user": {key: self.actor[key] for key in ("nexus_id", "role")}
                            if self.actor
                            else None,
                            "mode": "team" if service.team else "desktop",
                            "workspace": {"name": service.root.name, "path": str(service.root)},
                            "capabilities": {"expected_artifact_refs": True, "session_title": True},
                            "providers": service.team.providers(
                                service.providers(), self.actor["nexus_id"]
                            )
                            if service.team
                            else service.providers(),
                        },
                    )
                if route.startswith("/api/studio/"):
                    return self.send_payload(
                        200,
                        service.features.get(
                            route.strip("/").split("/")[2:],
                            parse_qs(urlparse(self.path).query),
                            self.actor,
                        ),
                    )
                if route in {"/api/metrics", "/api/workbench/metrics/prometheus"}:
                    text = service.telemetry.export_prometheus_metrics(self.actor)
                    return self.send_bytes(
                        200, text.encode("utf-8"), "text/plain; version=0.0.4; charset=utf-8"
                    )
                if route.startswith("/api/workbench/"):
                    return self.send_payload(
                        200,
                        service.workbench.get(
                            route.strip("/").split("/")[2:],
                            self.actor,
                            parse_qs(urlparse(self.path).query),
                        ),
                    )
                if route in {"/api/integrations", "/api/arc-capabilities"}:
                    from nexus.core.studio_integrations import arc_capabilities, integration_catalog

                    return self.send_payload(
                        200,
                        {
                            "items": integration_catalog(service)
                            if route == "/api/integrations"
                            else arc_capabilities()
                        },
                    )
                parts = route.strip("/").split("/")
                if len(parts) >= 3 and parts[:2] == ["api", "catalog"]:
                    kind = parts[2]
                    if len(parts) == 3:
                        return self.send_payload(
                            200,
                            {
                                "items": self.visible_items(
                                    service.catalog.list(kind, archived=True)
                                )
                                if kind == "artifacts"
                                else service.catalog.list(kind, archived=True)
                            },
                        )
                    if len(parts) == 4:
                        return self.send_payload(
                            200,
                            self.private_record(service.catalog.get(kind, parts[3], archived=True))
                            if kind == "artifacts"
                            else service.catalog.get(kind, parts[3], archived=True),
                        )
                    if len(parts) == 5 and parts[4] == "preview" and kind == "artifacts":
                        record = self.private_record(
                            service.catalog.get("artifacts", parts[3], archived=True)
                        )
                        query = parse_qs(urlparse(self.path).query)
                        revision = int(query["revision"][0]) if "revision" in query else None
                        if revision is not None and revision < 1:
                            raise ValueError("Revision must be a positive integer")
                        record = self.private_record(service.artifacts.revision(parts[3], revision))
                        return self.send_payload(
                            200,
                            service.artifacts.preview(
                                record, page=int(query.get("page", ["1"])[0])
                            ),
                        )
                    if len(parts) == 5 and parts[4] == "history":
                        if kind == "artifacts":
                            self.private_record(service.catalog.get(kind, parts[3], archived=True))
                        return self.send_payload(
                            200, {"items": service.catalog.history(kind, parts[3])}
                        )
                    if kind == "artifacts" and (
                        len(parts) == 5
                        and parts[4] == "download"
                        or len(parts) == 6
                        and parts[4] == "versions"
                    ):
                        self.private_record(service.catalog.get(kind, parts[3], archived=True))
                        record = self.private_record(
                            service.artifacts.revision(
                                parts[3], int(parts[5]) if len(parts) == 6 else None
                            )
                        )
                        filename, content = (
                            record["data"]["filename"],
                            service.artifacts.get(record),
                        )
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Length", str(len(content)))
                        self.send_header(
                            "Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename)
                        )
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(content)
                        return
                if route in {"/api/agents", "/api/workflows"}:
                    kind = route.rsplit("/", 1)[1]
                    return self.send_payload(200, {"items": service.definitions(kind)})
                if route in {"/api/sessions", "/api/runs", "/api/builds"}:
                    return self.send_payload(
                        200,
                        {
                            "items": self.visible_items(
                                [
                                    record
                                    for record in service.records(
                                        route.rsplit("/", 1)[1], limit=None if service.team else 100
                                    )
                                    if route != "/api/sessions" or not record.get("archived")
                                ]
                            )[:100]
                        },
                    )
                if route.startswith("/api/builds/"):
                    return self.send_payload(
                        200, self.private_record(service.builder.get(route.rsplit("/", 1)[1]))
                    )
                if route.startswith("/api/sessions/"):
                    return self.send_payload(
                        200, self.private_record(service.session(route.rsplit("/", 1)[1]))
                    )
                if route.startswith("/api/runs/"):
                    return self.send_payload(
                        200, self.private_record(service.run(route.rsplit("/", 1)[1]))
                    )
                if route.startswith("/api/workflows/") and route.endswith("/validate"):
                    return self.send_payload(200, service.validate_workflow(route.split("/")[-2]))
                if route in {"/api/evaluation-reports", "/api/schedule-state"}:
                    return self.send_payload(
                        200,
                        {
                            "items": self.visible_items(
                                service.records(parts[1], limit=None if service.team else 100)
                            )[:100]
                            if parts[1] == "evaluation-reports"
                            else service.records(parts[1])
                        },
                    )
                if route.startswith("/api/evaluation-reports/"):
                    return self.send_payload(
                        200,
                        self.private_record(
                            service._read(service._path("evaluation-reports", parts[-1]))
                        ),
                    )
                if route == "/api/mesh":
                    return self.send_payload(200, service.mesh_status())
                if route == "/api/export":
                    body = json.dumps(
                        service.export_definitions(), ensure_ascii=False, indent=2
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header(
                        "Content-Disposition", 'attachment; filename="nexus-workspace.json"'
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if route == "/api/tools":
                    from nexus.tools import tool_registry

                    tools = []
                    for name in tool_registry.names():
                        spec = tool_registry.get(name)
                        if spec and (not service.team or service.team.tool_allowed(name, service)):
                            tools.append(
                                {
                                    "id": name,
                                    "description": spec.description,
                                    "category": spec.category,
                                }
                            )
                    return self.send_payload(200, {"items": tools})
                self.send_payload(404, {"error": "Not found"})
            except PermissionError as exc:
                self.send_payload(403, {"error": str(exc)})
            except FileNotFoundError:
                self.send_payload(404, {"error": "Resource not found"})
            except (ValueError, OSError) as exc:
                self.send_payload(400, {"error": str(exc)})

        def handle_mcp(self, route):
            request_id = None
            try:
                parts = route.strip("/").split("/")
                if len(parts) != 3:
                    raise FileNotFoundError
                origin = self.headers.get("Origin")
                expected = (
                    service.team.public_url
                    if service.team
                    else f"http://{self.headers.get('Host')}"
                )
                if origin and origin != expected:
                    raise PermissionError("Cross-origin MCP requests are disabled")
                record = service.mcp_server.authorize(
                    parts[2], self.headers.get("Authorization", "").removeprefix("Bearer ")
                )
                if self.command != "POST":
                    self.send_response(405)
                    self.send_header("Allow", "POST")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("MCP requests use application/json")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("MCP request exceeds one MiB")
                request = json.loads(self.rfile.read(length))
                request_id = request.get("id") if isinstance(request, dict) else None
                result = service.mcp_server.dispatch(record, request)
                if result is None:
                    return self.send_payload(202, {})
                return self.send_payload(
                    200, {"jsonrpc": "2.0", "id": request_id, "result": result}
                )
            except PermissionError:
                return self.send_payload(401, {"error": "MCP entrypoint authorization required"})
            except FileNotFoundError:
                return self.send_payload(404, {"error": "Entrypoint not found"})
            except (ValueError, TypeError, KeyError) as exc:
                return self.send_payload(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": getattr(exc, "code", -32602),
                            "message": "Invalid MCP request",
                        },
                    },
                )

        def do_DELETE(self):
            if not self.trusted_host():
                return self.send_payload(403, {"error": "Unrecognized host"})
            route = urlparse(self.path).path
            parts = route.strip("/").split("/")
            if route.startswith("/api/a2a/") and parts[3:4] == ["rest"]:
                return self.handle_a2a_rest(route)
            if len(parts) == 3 and parts[0] == "api" and parts[1] in {"agents", "workflows"}:
                if not self.desktop_session():
                    return self.send_payload(401, _DESKTOP_SESSION_REQUIRED)
                self.actor = service.team.principal(self.headers) if service.team else None
                try:
                    authorized = secrets.compare_digest(
                        self.headers.get("X-Nexus-Studio-Token", ""),
                        service.team.csrf(token, self.actor)
                        if service.team and self.actor
                        else token,
                    )
                except (FileNotFoundError, ValueError, TypeError):
                    authorized = False
                if service.team and not self.actor:
                    return self.send_payload(
                        401, {"error": "Nexus sign-in required", "login_url": service.team.login_url}
                    )
                if not authorized:
                    return self.send_payload(
                        403, {"error": "Refresh the local workspace before continuing"}
                    )
                origin = self.headers.get("Origin")
                if origin and origin != (
                    service.team.public_url if service.team else f"http://{self.headers.get('Host')}"
                ):
                    return self.send_payload(403, {"error": "Cross-origin requests are not supported"})
                try:
                    if service.team:
                        service.team.authorize(self.actor, route)
                    return self.send_payload(200, service.delete_definition(parts[1], parts[2]))
                except PermissionError as exc:
                    return self.send_payload(403, {"error": str(exc)})
                except FileNotFoundError as exc:
                    return self.send_payload(404, {"error": str(exc)})
                except (ValueError, TypeError, OSError) as exc:
                    return self.send_payload(400, {"error": str(exc)})
            self.send_error(501, "Unsupported method")

        def do_POST(self):
            if not self.trusted_host():
                return self.send_payload(403, {"error": "Unrecognized host"})
            self.actor = service.team.principal(self.headers) if service.team else None
            route = urlparse(self.path).path
            parts = route.strip("/").split("/")
            if route.startswith("/api/a2a/"):
                if route.strip("/").split("/")[3:4] == ["rest"]:
                    return self.handle_a2a_rest(route)
                return self.handle_a2a(route)
            if route.startswith("/api/mcp/"):
                return self.handle_mcp(route)
            if len(parts) == 3 and parts[:2] == ["api", "openclaw"]:
                from nexus.core.studio_openclaw import OpenClawConflictError

                if self.headers.get("Origin"):
                    return self.send_payload(403, {"error": "Browser origins are not supported"})
                if self.headers.get_content_type() != "application/json":
                    return self.send_payload(415, {"error": "Expected JSON"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= _MAX_BODY:
                        return self.send_payload(413, {"error": "Request exceeds one MiB"})
                    return self.send_payload(200, service.openclaw.receive(
                        parts[2], dict(self.headers), self.rfile.read(length)
                    ))
                except OpenClawConflictError as exc:
                    return self.send_payload(409, {"error": str(exc)})
                except PermissionError:
                    return self.send_payload(403, {"error": "OpenClaw access denied; check current mappings and permissions"})
                except FileNotFoundError:
                    return self.send_payload(404, {"error": "OpenClaw resource not found"})
                except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                    return self.send_payload(400, {"error": "Invalid OpenClaw request; inspect its target, arguments and current run"})
                except Exception:
                    return self.send_payload(500, {"error": "OpenClaw request outcome uncertain; inspect Studio before retrying"})
            if len(parts) == 3 and parts[:2] == ["api", "a2a-callbacks"]:
                try:
                    if self.headers.get_content_type() != "application/json":
                        return self.send_payload(415, {"error": "Expected JSON"})
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 24 * 1024 * 1024:
                        return self.send_payload(413, {"error": "Callback exceeds size limit"})
                    result = service.external.callbacks.receive(
                        parts[2],
                        self.headers.get("Authorization", ""),
                        json.loads(self.rfile.read(length)),
                        self.headers.get("X-Nexus-Delivery-ID"),
                    )
                    return self.send_payload(202, result)
                except PermissionError:
                    return self.send_payload(403, {"error": "Invalid callback credential"})
                except (ValueError, TypeError, KeyError, RecursionError, FileNotFoundError):
                    return self.send_payload(400, {"error": "Invalid callback notification"})
            is_gw = len(parts) >= 2 and parts[0] == "gw"
            is_api_webhook = len(parts) >= 3 and parts[:2] == ["api", "webhooks"]
            if is_gw or is_api_webhook:
                if not self.trusted_host():
                    return self.send_payload(403, {"error": "Refresh the local workspace before continuing"})
                origin = self.headers.get("Origin")
                if origin and origin != (
                    service.team.public_url if service.team else f"http://{self.headers.get('Host')}"
                ):
                    return self.send_payload(403, {"error": "Cross-origin requests are not supported"})
                identifier = parts[1] if is_gw else parts[2]
                path_suffix = (
                    "/" + "/".join(parts[2:])
                    if is_gw and len(parts) > 2
                    else ("/" + "/".join(parts[3:]) if is_api_webhook and len(parts) > 3 else "")
                )
                ep = service.operations.find_entrypoint(identifier, path_suffix)
                if not ep:
                    return self.send_payload(404, {"error": "Entrypoint not found"})
                if not ep["data"].get("enabled"):
                    return self.send_payload(403, {"error": "Entrypoint is disabled"})
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= _MAX_BODY:
                    return self.send_payload(413, {"error": "Request exceeds the allowed upload size"})
                raw_body = self.rfile.read(length) if length > 0 else b""
                auth_mode = ep["data"].get("auth_mode", "bearer")
                if auth_mode == "hmac":
                    authorized = service.operations.authorize_entrypoint(
                        ep["id"], headers=dict(self.headers), raw_body=raw_body
                    )
                else:
                    bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    authorized = service.operations.authorize_entrypoint(ep["id"], bearer=bearer)
                if not authorized:
                    return self.send_payload(403, {"error": "Unauthorized entrypoint request"})
                try:
                    payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                    if not isinstance(payload, dict):
                        payload = {"task": str(payload), "payload": payload}
                except (ValueError, UnicodeDecodeError):
                    payload = {"task": raw_body.decode("utf-8", errors="replace")}
                if service.team:
                    payload.pop("requester_nexus_id", None)
                    payload.pop("_role", None)
                    payload.pop("owner_id", None)
                try:
                    result = service.operations.trigger_entrypoint(ep["id"], payload)
                    return self.send_payload(202, result)
                except ValueError as exc:
                    return self.send_payload(400, {"error": str(exc)})
            if len(parts) == 3 and parts[:2] == ["api", "channel-events"]:
                try:
                    challenge = parse_qs(urlparse(self.path).query).get("validationToken")
                    if challenge is not None:
                        if len(challenge) != 1:
                            raise ValueError("Invalid validation token")
                        return self.send_bytes(
                            200,
                            service.messaging.teams.challenge(parts[2], challenge[0]),
                            "text/plain; charset=utf-8",
                        )
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= _MAX_BODY:
                        raise ValueError("Incoming event must be at most one MiB")
                    return self.send_payload(
                        200,
                        service.messaging.receive(
                            parts[2], dict(self.headers), self.rfile.read(length)
                        ),
                    )
                except PermissionError as exc:
                    return self.send_payload(403, {"error": str(exc)})
                except (ValueError, FileNotFoundError, KeyError, TypeError, AttributeError):
                    return self.send_payload(400, {"error": "Invalid incoming channel event"})
            if not self.desktop_session():
                return self.send_payload(401, _DESKTOP_SESSION_REQUIRED)
            try:
                authorized = secrets.compare_digest(
                    self.headers.get("X-Nexus-Studio-Token", ""),
                    service.team.csrf(token, self.actor)
                    if service.team and self.actor
                    else token,
                )
            except (FileNotFoundError, ValueError, TypeError):
                authorized = False
            if service.team and not self.actor:
                return self.send_payload(
                    401, {"error": "Nexus sign-in required", "login_url": service.team.login_url}
                )
            if not self.trusted_host() or not authorized:
                return self.send_payload(
                    403, {"error": "Refresh the local workspace before continuing"}
                )
            origin = self.headers.get("Origin")
            if origin and origin != (
                service.team.public_url if service.team else f"http://{self.headers.get('Host')}"
            ):
                return self.send_payload(403, {"error": "Cross-origin requests are not supported"})
            if self.headers.get_content_type() != "application/json":
                return self.send_payload(415, {"error": "Expected a JSON request"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body_limit = (
                    24 * 1024 * 1024
                    if parts
                    in (["api", "catalog", "artifacts"], ["api", "studio", "projects", "import"], ["api", "studio", "files", "upload"])
                    else 6 * 1024 * 1024
                    if parts
                    in (["api", "workbench", "transcriptions"], ["api", "studio", "speech"])
                    else _MAX_BODY
                )
                if not 0 < length <= body_limit:
                    return self.send_payload(
                        413, {"error": "Request exceeds the allowed upload size"}
                    )
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Request must be an object")
                route = urlparse(self.path).path
                parts = route.strip("/").split("/")
                if service.team:
                    payload.pop("requester_nexus_id", None)
                    payload.pop("_role", None)
                    payload.pop("owner_id", None)
                    service.team.authorize(self.actor, route, payload)
                    payload["requester_nexus_id"] = self.actor["nexus_id"]
                    payload["_role"] = self.actor["role"]
                    if len(parts) == 4 and parts[1] == "builds":
                        self.private_record(service.builder.get(parts[2]))
                    if len(parts) == 4 and parts[1] == "runs":
                        self.private_record(service.run(parts[2]))
                    if (
                        payload.get("build_id")
                        and service._path("builds", payload["build_id"]).exists()
                    ):
                        self.private_record(service.builder.get(payload["build_id"]))
                    if (
                        route == "/api/catalog/artifacts"
                        and payload.get("id")
                        and service.catalog._path("artifacts", payload["id"]).exists()
                    ):
                        self.private_record(
                            service.catalog.get("artifacts", payload["id"], archived=True)
                        )
                    if len(parts) == 5 and parts[1:3] == ["catalog", "artifacts"]:
                        self.private_record(
                            service.catalog.get("artifacts", parts[3], archived=True)
                        )
                if (
                    len(parts) == 6
                    and parts[:3] == ["api", "studio", "connectors"]
                    and parts[4:] == ["oauth", "start"]
                ):
                    origin = (
                        service.team.public_url
                        if service.team
                        else "http://" + self.headers["Host"]
                    )
                    result, cookie = service.oauth.start(parts[3], self.actor, origin)
                    return self.send_payload(200, result, headers={"Set-Cookie": cookie})
                if parts[:2] == ["api", "studio"]:
                    status, result = service.features.post(parts[2:], payload, self.actor)
                    return self.send_payload(status, result)
                if parts[:2] == ["api", "workbench"]:
                    status, result = service.workbench.post(parts[2:], payload, self.actor)
                    return self.send_payload(status, result)
                if len(parts) == 4 and parts[:2] == ["api", "connectors"] and parts[3] == "test":
                    return self.send_payload(
                        200,
                        {
                            "output": service.operations.call_connector(
                                parts[2], payload, self.actor["nexus_id"] if self.actor else None
                            )
                        },
                    )
                if len(parts) == 4 and parts[:2] == ["api", "entrypoints"] and parts[3] == "invoke":
                    return self.send_payload(
                        202, service.operations.trigger_entrypoint(parts[2], payload)
                    )

                if route == "/api/catalog/skills/import":
                    return self.send_payload(200, service.skill_import.import_skill(payload))
                if len(parts) >= 3 and parts[:2] == ["api", "catalog"]:
                    if len(parts) == 3:
                        return self.send_payload(201, service.catalog.save(parts[2], payload))
                    if len(parts) == 5 and parts[4] == "archive":
                        return self.send_payload(
                            200, service.catalog.archive(parts[2], parts[3], payload)
                        )
                if route in {"/api/agents", "/api/workflows"}:
                    return self.send_payload(
                        201, service.save_definition(route.rsplit("/", 1)[1], payload)
                    )
                if route == "/api/builds":
                    return self.send_payload(202, service.start_run({**payload, "kind": "build"}))
                parts = route.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "builds"]:
                    if parts[3] == "reopen":
                        self.private_record(service.builder.get(parts[2]))
                        return self.send_payload(200, service.builder.reopen(parts[2], payload))
                    if parts[3] == "apply":
                        return self.send_payload(201, service.builder.apply(parts[2], payload))
                    if parts[3] == "test":
                        return self.send_payload(202, service.builder.test(parts[2], payload))
                if (
                    len(parts) == 4
                    and parts[0] == "api"
                    and parts[1] in {"agents", "workflows"}
                    and parts[3] == "update"
                ):
                    return self.send_payload(
                        200, service.update_definition(parts[1], parts[2], payload)
                    )
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "resume":
                    return self.send_payload(
                        202,
                        service.graph.resume(
                            parts[2], payload.get("decision"),
                            checkpoint=payload.get("evaluation_checkpoint"),
                        ),
                    )
                if route == "/api/runs":
                    return self.send_payload(
                        202,
                        service.start_run(
                            payload,
                            callback_origin=service.team.public_url
                            if service.team
                            else f"http://{self.headers.get('Host')}",
                        ),
                    )
                self.send_payload(404, {"error": "Not found"})
            except PermissionError as exc:
                self.send_payload(403, {"error": str(exc)})
            except FileExistsError:
                self.send_payload(
                    409, {"error": "This definition already exists; choose another name"}
                )
            except FileNotFoundError as exc:
                self.send_payload(404, {"error": str(exc)})
            except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
                self.send_payload(400, {"error": str(exc)})

    return StudioHandler


def serve_studio(
    root: str | Path = ".",
    port: int = 5003,
    *,
    host: str = "127.0.0.1",
    team_config: str | None = None,
    state_dir: str | None = None,
    runtime_config: str | None = None,
    worker_only: bool = False,
    launch: DesktopLaunchCredential | None = None,
    parent_input=None,
) -> None:
    """Run desktop mode, or an explicitly configured authenticated team service."""
    policy, transport = None, None
    if launch and team_config:
        raise ValueError("A native desktop launch credential applies only to desktop mode")
    if parent_input is not None and (launch is None or worker_only):
        raise ValueError("Parent lifetime monitoring requires a native desktop HTTP launch")
    if team_config:
        from nexus.core.studio_team import load_team_configuration

        if Path(team_config).resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("Team access configuration must live outside the shared project")
        policy, transport = load_team_configuration(team_config)
    elif host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Public binding requires explicit team authentication configuration")
    if launch:
        # Native launches only; terminal `nexus studio` keeps its existing folders.
        check_workspace_root(root)
    lock = None
    if not team_config and not worker_only:
        # Taken before the workspace is touched. A quitting native instance may
        # still be closing, so a native launch waits briefly for it.
        lock = WorkspaceLock(root)
        if not lock.acquire(port, metadata={"mode": "desktop"}, wait=5.0 if launch else 0.0):
            holder = lock.read() or {}
            raise WorkspaceInUseError(
                "Workspace is already open in another Nexus Studio"
                + (f" (process {holder['pid']}, port {holder['port']})" if "pid" in holder else "")
            )
    with termination_requested() as stop:
        service, server = None, None
        try:
            from nexus.core.studio_runtime import load_runtime_configuration

            options = load_runtime_configuration(runtime_config, root) if runtime_config else {}
            service = StudioService(
                root, team_policy=policy, state_dir=state_dir, transport=transport, **options
            )
            if stop.is_set():
                return
            if worker_only:
                if not service.sql_state or not service.executor.threads:
                    raise ValueError(
                        "Worker-only mode requires SQL state and at least one configured worker"
                    )
                service.operations.start()
                print(f"Nexus Studio worker: {service.sql_state.namespace}", flush=True)
                stop.wait()
                return
            server = ThreadingHTTPServer(
                (host, port), make_studio_handler(service, secrets.token_urlsafe(32), launch)
            )
            if parent_input is not None:
                from nexus.core.studio_desktop_parent import watch_parent_input

                watch_parent_input(parent_input, server.shutdown)
            service.operations.start()
            print(
                f"Nexus Studio: {policy.public_url if policy else f'http://{host}:{server.server_port}'}"
            )
            print(f"Workspace: {service.root}")
            serve_until_stopped(server, stop)
        except KeyboardInterrupt:
            pass
        finally:
            if parent_input is not None:
                import os
                import threading

                # The shell may already be gone; never let a hung close outlive it.
                watchdog = threading.Timer(10, os._exit, (1,))
                watchdog.daemon = True
                watchdog.start()
            if server:
                server.server_close()
            if service:
                service.close()
            elif transport:
                transport.close()
            if lock:
                lock.release()
        # Only after a normal shutdown: a startup error must reach the shell as its
        # message and exit status, not as a self-inflicted SIGKILL.
        if parent_input is not None:
            end_owned_process_group()
