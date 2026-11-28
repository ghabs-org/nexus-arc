"""Portable OpenAPI references, selected security and wire serialization."""

import copy
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_openapi import _validate, call_openapi, operations, validate_openapi
from nexus.core.studio_openapi_schema import normalized_schema


def spec():
    return {
        "openapi": "3.0.3",
        "components": {
            "parameters": {
                "identifier": {
                    "name": "id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            },
            "securitySchemes": {"account": {"type": "apiKey", "in": "header", "name": "X-Account"}},
            "schemas": {
                "Body": {
                    "type": "object",
                    "required": ["profile"],
                    "properties": {
                        "profile": {
                            "type": "object",
                            "properties": {"age": {"type": "integer", "nullable": True}},
                            "required": ["age"],
                        }
                    },
                }
            },
            "requestBodies": {
                "payload": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Body"}}
                    },
                }
            },
        },
        "security": [{"account": []}],
        "paths": {
            "/items/{id}": {
                "parameters": [
                    {"$ref": "#/components/parameters/identifier"},
                    {
                        "name": "tags",
                        "in": "query",
                        "schema": {"type": "array", "items": {"type": "integer"}},
                    },
                ],
                "get": {
                    "operationId": "read",
                    "parameters": [
                        {
                            "name": "tags",
                            "in": "query",
                            "style": "pipeDelimited",
                            "schema": {"type": "array", "items": {"type": "integer"}},
                        },
                        {
                            "name": "filter",
                            "in": "query",
                            "style": "deepObject",
                            "schema": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            },
                        },
                    ],
                },
                "post": {
                    "operationId": "write",
                    "requestBody": {"$ref": "#/components/requestBodies/payload"},
                },
            }
        },
    }


def data(document=None):
    return {
        "type": "openapi",
        "url": "https://example.test",
        "spec": document or spec(),
        "operations": ["read", "write"],
        "auth": {"type": "api-key", "token_env": "ACCOUNT_KEY", "header": "X-Account"},
        "enabled": True,
        "actions": ["read", "invoke"],
    }


def test_parameter_override_and_local_body_references():
    ops = operations(spec())
    assert len(ops["read"]["parameters"]) == 3
    assert ops["read"]["parameters"][1]["style"] == "pipeDelimited"
    assert ops["write"]["requestBody"]["required"]
    assert validate_openapi(data())["auth"]["header"] == "X-Account"
    _validate(spec(), {"$ref": "#/components/schemas/Body"}, {"profile": {"age": None}})
    with pytest.raises(ValueError):
        _validate(spec(), {"$ref": "#/components/schemas/Body"}, {"profile": {"age": "bad"}})


@pytest.mark.parametrize(
    "auth",
    [
        {"type": "none"},
        {"type": "bearer", "token_env": "ACCOUNT_KEY"},
        {"type": "api-key", "token_env": "ACCOUNT_KEY", "header": "X-Wrong"},
    ],
)
def test_selected_security_must_match_before_save(auth):
    with pytest.raises(ValueError, match="security"):
        validate_openapi({**data(), "auth": auth})


def test_security_alternatives_and_explicit_public_operation():
    document = spec()
    document["security"] = [{}, {"account": []}]
    validate_openapi({**data(document), "auth": {"type": "none"}})
    document["security"] = [{"missing": []}]
    with pytest.raises(ValueError, match="security"):
        validate_openapi(data(document))


def test_nullable_normalization_preserves_property_names_and_data():
    source = {
        "type": "object",
        "properties": {
            "nullable": {"type": "boolean"},
            "age": {"type": "number", "nullable": True, "minimum": 0, "exclusiveMinimum": True},
        },
        "example": {"nullable": True, "type": "integer"},
    }
    result = normalized_schema(source, "3.0.3")
    assert "nullable" in result["properties"]
    assert result["example"] == source["example"]
    assert result["properties"]["age"]["exclusiveMinimum"] == 0
    assert "minimum" not in result["properties"]["age"]
    assert source["properties"]["age"]["nullable"] is True


@pytest.mark.parametrize("mutation", ["external", "cycle", "style", "duplicate", "media"])
def test_unsupported_or_ambiguous_contract_rejected(mutation):
    document = spec()
    if mutation == "external":
        document["components"]["parameters"]["identifier"] = {
            "$ref": "https://unapproved.invalid/schema"
        }
    if mutation == "cycle":
        document["components"]["parameters"]["identifier"] = {
            "$ref": "#/components/parameters/identifier"
        }
    if mutation == "style":
        document["paths"]["/items/{id}"]["get"]["parameters"][0]["style"] = "matrix"
    if mutation == "duplicate":
        document["paths"]["/items/{id}"]["parameters"].append(
            copy.deepcopy(document["paths"]["/items/{id}"]["parameters"][0])
        )
    if mutation == "media":
        document["components"]["requestBodies"]["payload"]["content"] = {"application/xml": {}}
    with pytest.raises(ValueError):
        validate_openapi(data(document))


def test_actual_wire_serialization_and_credential_header_protection(tmp_path, monkeypatch):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            observed.append((self.path, self.headers.get("X-Account")))
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    studio = StudioService(tmp_path)
    monkeypatch.setenv("ACCOUNT_KEY", "synthetic-account-key")
    value = validate_openapi(
        {**data(), "url": f"http://127.0.0.1:{server.server_port}", "allow_private": True}
    )
    try:
        call_openapi(
            studio,
            value,
            None,
            "read",
            {
                "operation_id": "read",
                "parameters": {"id": "item", "tags": [1, 2], "filter": {"city": "Rome"}},
            },
        )
        parsed = urlparse(observed[0][0])
        assert parsed.path == "/items/item"
        assert parse_qs(parsed.query) == {"tags": ["1|2"], "filter[city]": ["Rome"]}
        assert observed[0][1] == "synthetic-account-key"
        document = spec()
        document["paths"]["/items/{id}"]["get"]["parameters"].append(
            {"name": "X-Account", "in": "header", "schema": {"type": "string"}}
        )
        value["spec"] = document
        with pytest.raises(ValueError, match="credential"):
            call_openapi(
                studio,
                value,
                None,
                "read",
                {"operation_id": "read", "parameters": {"id": "item", "X-Account": "override"}},
            )
        assert len(observed) == 1
    finally:
        studio.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
