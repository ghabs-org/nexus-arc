"""Selected OpenAPI operations become bounded, identity-scoped HTTP tools."""

import json
import re
from urllib.parse import quote

from nexus.core.studio_openapi_schema import (
    auth_matches,
    local,
    normalized_schema,
    parameters,
    serialize,
)
from nexus.core.studio_remote import (
    remote_headers,
    remote_request_url,
    validate_destination,
    validate_remote,
)


def operations(spec):
    if not isinstance(spec, dict) or not re.fullmatch(
        r"3\.[01]\.\d+", str(spec.get("openapi", ""))
    ):
        raise ValueError("Import an OpenAPI 3.x document")
    if len(json.dumps(spec)) > 512000:
        raise ValueError("OpenAPI definition exceeds 512000 characters")
    result = {}
    for path, entry in spec.get("paths", {}).items():
        if (
            not path.startswith("/")
            or path.startswith("//")
            or ".." in path.split("/")
            or "?" in path
            or "#" in path
        ):
            raise ValueError("OpenAPI paths must be relative paths without traversal")
        entry = local(spec, entry)
        for method, operation in entry.items():
            if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            operation = local(spec, operation)
            identity = operation.get("operationId") or f"{method}:{path}"
            if identity in result:
                raise ValueError("OpenAPI operation IDs must be unique")
            params = parameters(spec, entry, operation)
            result[identity] = {
                "path": path,
                "method": method.upper(),
                "parameters": params,
                "requestBody": local(spec, operation.get("requestBody", {})),
                "security": operation.get("security", spec.get("security")),
                "summary": str(operation.get("summary", ""))[:500],
            }
    if not 1 <= len(result) <= 200:
        raise ValueError("Import between one and 200 operations")
    return result


def validate_openapi(data):
    remote = validate_remote(data)
    spec = data.get("spec")
    available = operations(spec)
    selected = data.get("operations", [])
    if (
        not isinstance(selected, list)
        or not selected
        or any(op not in available for op in selected)
    ):
        raise ValueError("Select existing OpenAPI operation IDs")
    actions = data.get("actions", ["list_operations", "read"])
    if not isinstance(actions, list) or set(actions) - {"list_operations", "read", "invoke"}:
        raise ValueError("Choose list_operations, read or invoke OpenAPI actions")
    if type(data.get("enabled", False)) is not bool:
        raise ValueError("Enabled must be true or false")

    # Never fetch external schemas or let a specification redirect credentials.
    def refs(value):
        if isinstance(value, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in value and not str(value[key]).startswith("#/"):
                    raise ValueError("Only local OpenAPI component references are supported")
            for v in value.values():
                refs(v)
        elif isinstance(value, list):
            for v in value:
                refs(v)

    refs(spec)
    result = {
        "type": "openapi",
        **remote,
        "spec": spec,
        "operations": list(dict.fromkeys(selected)),
        "actions": actions,
        "enabled": data.get("enabled", False),
    }
    health = data.get("health_operation", "")
    if not isinstance(health, str) or len(health) > 200:
        raise ValueError("Health operation must be an operation ID up to 200 characters")
    if health:
        operation = available.get(health)
        if (
            health not in selected
            or "read" not in actions
            or not operation
            or operation["method"] not in {"GET", "HEAD", "OPTIONS"}
            or operation["requestBody"]
            or re.search(r"\{[^}]+\}", operation["path"])
            or any(p.get("required") or p["in"] == "path" for p in operation["parameters"])
        ):
            raise ValueError(
                "Choose a selected read-only health operation without required parameters or a request body"
            )
        result["health_operation"] = health
    for name in selected:
        operation = available[name]
        auth_matches(spec, operation["security"], remote["auth"])
        body = operation["requestBody"]
        if body and (
            not isinstance(body.get("content"), dict)
            or set(body["content"]) != {"application/json"}
        ):
            raise ValueError(
                "Selected operation body requires application/json; other media types need adaptation"
            )
    return result


def call_openapi(studio, data, identity, action, arguments):
    import requests

    available = operations(data["spec"])
    if action == "list_operations":
        return [
            {"id": name, "method": available[name]["method"], "summary": available[name]["summary"]}
            for name in data["operations"]
        ]
    name = arguments.get("operation_id")
    if name not in data["operations"]:
        raise PermissionError("Operation is not enabled in this connector")
    operation = available[name]
    auth_matches(data["spec"], operation["security"], data.get("auth", {}))
    if action == "read" and operation["method"] not in {"GET", "HEAD", "OPTIONS"}:
        raise PermissionError("Mutating operations require invoke and explicit approval")
    values = arguments.get("parameters", {})
    if not isinstance(values, dict):
        raise ValueError("OpenAPI parameters must be an object")
    names = [p["name"] for p in operation["parameters"]]
    keys = {p["in"] + ":" + p["name"] for p in operation["parameters"]}
    keys.update(name for name in names if names.count(name) == 1)
    if set(values) - keys:
        raise ValueError(
            "Unknown or ambiguous operation parameter; qualify duplicate names by location"
        )
    headers, params, path = remote_headers(studio, data, identity), {}, operation["path"]
    for parameter in operation["parameters"]:
        key = parameter["name"]
        qualified = parameter["in"] + ":" + key
        selected_key = qualified if qualified in values else key
        if selected_key not in values:
            if parameter.get("required") or parameter["in"] == "path":
                raise ValueError(f"Missing operation parameter: {key}")
            continue
        if qualified in values and key in values:
            raise ValueError("Supply each parameter once")
        value = values[selected_key]
        schema = parameter.get(
            "schema", parameter.get("content", {}).get("application/json", {}).get("schema", {})
        )
        _validate(data["spec"], schema, value)
        value = serialize(parameter, value)
        if parameter["in"] == "path":
            text = str(value)
            if text in {".", ".."} or "/" in text or "\\" in text:
                raise ValueError("Path values cannot contain path separators or traversal")
            path = path.replace("{" + key + "}", quote(text, safe=""))
        elif parameter["in"] == "query":
            if data.get("auth", {}).get("type") == "query" and key == data["auth"]["parameter"]:
                raise ValueError("Operation cannot override its account credential parameter")
            entries = value if isinstance(value, dict) else {key: value}
            for query_key, query_value in entries.items():
                if (
                    query_key in params
                    or data.get("auth", {}).get("type") == "query"
                    and query_key == data["auth"]["parameter"]
                ):
                    raise ValueError("Operation query parameters collide or override credentials")
                params[query_key] = query_value
        else:
            if (
                key.lower() in {"host", "authorization", "cookie", "connection", "content-length"}
                or key.lower() in {h.lower() for h in headers}
                or "\r" in str(value)
                or "\n" in str(value)
            ):
                raise ValueError("Operation cannot override credential or transport headers")
            headers[key] = str(value)
    if re.search(r"\{[^}]+\}", path):
        raise ValueError("Unresolved OpenAPI path parameter")
    body = arguments.get("body")
    body_config = operation["requestBody"]
    if body_config:
        schema = body_config.get("content", {}).get("application/json", {}).get("schema")
        if schema is None:
            raise ValueError("This operation requires an unsupported request body format")
        if body is None and body_config.get("required"):
            raise ValueError("Operation requires a JSON request body")
        if body is not None:
            _validate(data["spec"], schema, body)
    elif body is not None:
        raise ValueError("Operation does not define a request body")
    validate_destination(data["url"], data.get("allow_private", False))
    try:
        with requests.request(
            operation["method"],
            remote_request_url(studio, {**data, "url": data["url"] + path}, identity),
            params=params,
            headers=headers,
            json=body,
            timeout=(10, 30),
            allow_redirects=False,
            stream=True,
        ) as response:
            if not 200 <= response.status_code < 300:
                raise ValueError("Remote operation returned an unsuccessful status")
            raw = bytearray()
            for chunk in response.iter_content(16384):
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise ValueError("Remote operation response exceeds 256 KiB")
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                return raw.decode("utf-8", errors="replace")
    except requests.RequestException as exc:
        raise ValueError(
            "Remote operation failed; verify account access and service availability"
        ) from exc


def _validate(spec, schema, value):
    def check_refs(part):
        if isinstance(part, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in part and not str(part[key]).startswith("#"):
                    raise ValueError("External schema references are disabled")
            for item in part.values():
                check_refs(item)
        elif isinstance(part, list):
            for item in part:
                check_refs(item)

    check_refs(schema)
    try:
        import jsonschema
    except ImportError as exc:
        raise ValueError("Install nexus-arc[studio-connectors] for OpenAPI validation") from exc
    root = normalized_schema(
        {"components": spec.get("components", {}), **schema}, spec.get("openapi")
    )
    try:
        jsonschema.Draft202012Validator(root).validate(value)
    except Exception as exc:
        raise ValueError("Operation argument does not match its declared schema") from exc
