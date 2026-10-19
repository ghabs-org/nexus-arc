import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_connections import validate_connection
from nexus.core.studio_openapi import _validate, validate_openapi
from nexus.core.studio_remote import remote_headers, validate_destination

SPEC = {
    "openapi": "3.0.3",
    "paths": {
        "/notes/{id}": {
            "parameters": [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
            ],
            "get": {"operationId": "readNote"},
            "delete": {"operationId": "deleteNote"},
        }
    },
}


def test_remote_mcp_normalization_does_not_install_or_run_executables():
    data = validate_connection(
        {
            "type": "mcp",
            "transport": "streamable-http",
            "url": "https://mcp.example.test/tools",
            "tools": ["search"],
            "read_only_tools": ["search"],
        }
    )
    assert data["command"] == "" and data["transport"] == "streamable-http"
    assert data["actions"] == ["discover"]
    with pytest.raises(ValueError, match="HTTPS"):
        validate_connection({"type": "mcp", "transport": "sse", "url": "http://localhost/mcp"})
    with pytest.raises(ValueError, match="credential"):
        validate_connection(
            {"type": "mcp", "transport": "sse", "url": "https://user:secret@example.test/mcp"}
        )


def test_remote_headers_reuse_account_environment_and_block_private_networks(tmp_path, monkeypatch):
    service = StudioService(tmp_path)
    try:
        monkeypatch.setenv("TEST_REMOTE_TOKEN", "fixture-token")
        data = validate_connection(
            {
                "type": "mcp",
                "transport": "sse",
                "url": "https://example.test/mcp",
                "auth": {
                    "type": "api-key",
                    "token_env": "TEST_REMOTE_TOKEN",
                    "header": "X-Service-Key",
                },
            }
        )
        assert remote_headers(service, data, None) == {"X-Service-Key": "fixture-token"}
        with pytest.raises(ValueError, match="Private"):
            validate_destination("https://127.0.0.1/mcp")
    finally:
        service.close()


def test_openapi_local_refs_reject_invalid_arguments_and_external_schemas():
    _validate(
        {"components": {"schemas": {"Id": {"type": "integer"}}}},
        {"$ref": "#/components/schemas/Id"},
        3,
    )
    with pytest.raises(ValueError, match="schema"):
        _validate({}, {"type": "integer"}, "three")
    spec = {**SPEC, "components": {"schemas": {"Bad": {"$ref": "https://private.example/schema"}}}}
    with pytest.raises(ValueError, match="local"):
        validate_openapi({"url": "https://example.test", "spec": spec, "operations": ["readNote"]})


def test_openapi_real_http_path_validation_no_mutation_replay_and_selected_operations(tmp_path):
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_seen.append(("GET", self.path))
            body = b'{"note":"evidence"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self):
            requests_seen.append(("DELETE", self.path))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = StudioService(tmp_path)
    try:
        record = service.catalog.save(
            "connectors",
            {
                "name": "Notes API",
                "data": {
                    "type": "openapi",
                    "url": f"http://127.0.0.1:{server.server_port}",
                    "allow_private": True,
                    "spec": SPEC,
                    "operations": ["readNote", "deleteNote"],
                    "actions": ["read", "invoke", "list_operations"],
                    "enabled": True,
                },
            },
        )

        def call(action, arguments, **extra):
            return asyncio.run(
                service.connections.execute(
                    record["id"], {"action": action, "arguments": arguments, **extra}
                )
            )

        assert call("read", {"operation_id": "readNote", "parameters": {"id": 2}}) == {
            "note": "evidence"
        }
        with pytest.raises(PermissionError, match="approval"):
            call("read", {"operation_id": "deleteNote", "parameters": {"id": 2}})
        with pytest.raises(ValueError, match="confirm"):
            call("invoke", {"operation_id": "deleteNote", "parameters": {"id": 2}})
        call("invoke", {"operation_id": "deleteNote", "parameters": {"id": 2}}, confirm=True)
        assert requests_seen == [("GET", "/notes/2"), ("DELETE", "/notes/2")]
        with pytest.raises(ValueError):
            call("read", {"operation_id": "readNote", "parameters": {"id": "../outside"}})
    finally:
        service.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sql_writes_require_explicit_configuration():
    data = validate_connection({"type": "sql", "dialect": "postgresql", "dsn_env": "NEXUS_SQL_DSN"})
    assert data["actions"] == ["query", "inspect_schema"] and not data["allow_writes"]
    with pytest.raises(ValueError, match="writes"):
        validate_connection(
            {
                "type": "sql",
                "dialect": "postgresql",
                "dsn_env": "NEXUS_SQL_DSN",
                "actions": ["execute"],
            }
        )


def test_select_into_file_exports_and_remote_execution_never_reach_database(tmp_path):
    service = StudioService(tmp_path)
    data = validate_connection(
        {"type": "sql", "dialect": "mssql", "dsn_env": "UNCONFIGURED_READ_TEST"}
    )
    try:
        # No driver or credentials are needed to reject these before connection.
        for query in (
            "SELECT * INTO new_table FROM source",
            "SELECT * INTO OUTFILE '/tmp/export' FROM source",
            "SELECT * FROM OPENROWSET('provider', 'secret', 'query')",
            "SELECT LOAD_FILE(:path)",
            "SELECT * FROM data FOR UPDATE",
            "SELECT 1; DELETE FROM data",
        ):
            with pytest.raises(ValueError, match="Read queries"):
                service.connections.sql.call(data, None, "query", {"query": query})
        from nexus.core.studio_sql_connectors import validate_read_query

        assert validate_read_query("SELECT SUM(amount) AS total FROM data WHERE label = :label")
    finally:
        service.close()
