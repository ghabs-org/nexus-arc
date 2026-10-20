"""Bounded local OpenAPI references, parameter contracts and selected auth policy."""

import json


def local(spec, value, chain=()):
    if not isinstance(value, dict):
        raise ValueError("OpenAPI definitions must be objects")
    if "$ref" not in value:
        return value
    reference = value["$ref"]
    if (
        not isinstance(reference, str)
        or not reference.startswith("#/")
        or reference in chain
        or len(chain) >= 20
    ):
        raise ValueError("Resolve cyclic/external OpenAPI object references before import")
    target = spec
    try:
        for key in reference[2:].split("/"):
            target = target[key.replace("~1", "/").replace("~0", "~")]
    except (KeyError, TypeError):
        raise ValueError("Unresolved local OpenAPI reference") from None
    target = local(spec, target, (*chain, reference))
    return {**target, **{k: v for k, v in value.items() if k != "$ref"}}


def parameters(spec, path, operation):
    result = {}
    for source in (path.get("parameters", []), operation.get("parameters", [])):
        if not isinstance(source, list) or len(source) > 100:
            raise ValueError("OpenAPI parameters must be bounded lists")
        seen = set()
        for value in source:
            parameter = local(spec, value)
            name, location = parameter.get("name"), parameter.get("in")
            if (
                not isinstance(name, str)
                or not name
                or len(name) > 200
                or location not in {"path", "query", "header"}
            ):
                raise ValueError("Supported parameter locations are path, query and header")
            key = (location, name.lower() if location == "header" else name)
            if key in seen:
                raise ValueError("Duplicate OpenAPI parameter in one scope")
            seen.add(key)
            style = parameter.get("style", "form" if location == "query" else "simple")
            if style not in (
                {"form", "spaceDelimited", "pipeDelimited", "deepObject"}
                if location == "query"
                else {"simple"}
            ):
                raise ValueError("Unsupported OpenAPI parameter serialization style")
            if type(parameter.get("explode", style == "form")) is not bool:
                raise ValueError("Parameter explode must be boolean")
            if "schema" in parameter and "content" in parameter:
                raise ValueError("Choose parameter schema or content")
            if "content" in parameter:
                content = parameter["content"]
                if not isinstance(content, dict) or set(content) != {"application/json"}:
                    raise ValueError("Parameter content requires application/json")
            result[key] = parameter
    return list(result.values())


def auth_matches(spec, security, auth):
    if security is None or security == []:
        return
    if not isinstance(security, list) or len(security) > 20:
        raise ValueError("OpenAPI security must list bounded alternatives")
    schemes = spec.get("components", {}).get("securitySchemes", {})

    def match(name, scopes):
        if (
            name not in schemes
            or not isinstance(scopes, list)
            or any(not isinstance(s, str) for s in scopes)
        ):
            raise ValueError("Unknown OpenAPI security scheme or invalid scopes")
        scheme = local(spec, schemes[name])
        kind = scheme.get("type")
        if kind == "http":
            return str(scheme.get("scheme", "")).lower() == auth.get("type") and not scopes
        if kind == "apiKey":
            expected = (
                "api-key"
                if scheme.get("in") == "header"
                else "query"
                if scheme.get("in") == "query"
                else None
            )
            field = "header" if expected == "api-key" else "parameter"
            a, b = scheme.get("name"), auth.get(field)
            return (
                expected is not None
                and auth.get("type") == expected
                and isinstance(a, str)
                and (a.lower() == str(b).lower() if expected == "api-key" else a == b)
                and not scopes
            )
        if kind in {"oauth2", "openIdConnect"}:
            # Opaque bearer scopes are enforced by the selected resource server.
            return auth.get("type") == "bearer"
        return False

    valid = False
    for alternative in security:
        if not isinstance(alternative, dict):
            raise ValueError("Invalid OpenAPI security alternative")
        matched = [match(name, scopes) for name, scopes in alternative.items()]
        # One configured credential may satisfy aliases for the same scheme.
        valid |= all(matched)
    if not valid:
        raise ValueError("Selected operation security does not match configured authentication")


def normalized_schema(value, version, depth=0):
    if depth > 80:
        raise ValueError("OpenAPI schema nesting limit exceeded")
    if isinstance(value, list):
        return [normalized_schema(v, version, depth + 1) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key in {"default", "example", "examples", "const", "enum"}:
            result[key] = child
        elif key in {
            "properties",
            "$defs",
            "definitions",
            "patternProperties",
            "schemas",
        } and isinstance(child, dict):
            result[key] = {
                name: normalized_schema(v, version, depth + 1) for name, v in child.items()
            }
        else:
            result[key] = normalized_schema(child, version, depth + 1)
    if str(version).startswith("3.0"):
        if result.pop("nullable", False) and "type" in result:
            result["type"] = list(
                dict.fromkeys(
                    [
                        *(result["type"] if isinstance(result["type"], list) else [result["type"]]),
                        "null",
                    ]
                )
            )
        for bound in ("Minimum", "Maximum"):
            key = "exclusive" + bound
            if isinstance(result.get(key), bool):
                flag = result.pop(key)
                if flag and bound.lower() in result:
                    result[key] = result.pop(bound.lower())
    return result


def scalar(value):
    if value is None:
        return ""
    if type(value) is bool:
        return str(value).lower()
    if isinstance(value, (dict, list)):
        raise ValueError("Nested parameter structures require JSON content serialization")
    return str(value)


def serialize(parameter, value):
    if "content" in parameter:
        return json.dumps(value, separators=(",", ":"), allow_nan=False)
    style = parameter.get("style", "form" if parameter["in"] == "query" else "simple")
    explode = parameter.get("explode", style == "form")
    if isinstance(value, list):
        values = [scalar(v) for v in value]
        if parameter["in"] == "query" and style == "form" and explode:
            return values
        return ({"spaceDelimited": " ", "pipeDelimited": "|"}.get(style, ",")).join(values)
    if isinstance(value, dict):
        values = {str(k): scalar(v) for k, v in value.items()}
        if parameter["in"] == "query" and style == "deepObject":
            return {parameter["name"] + "[" + key + "]": v for key, v in values.items()}
        if parameter["in"] == "query" and style == "form" and explode:
            return values
        return (
            ",".join(k + "=" + v for k, v in values.items())
            if explode
            else ",".join(v for pair in values.items() for v in pair)
        )
    return scalar(value)
