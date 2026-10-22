"""Terminal discovery and directed-message parsing over existing Studio agents."""

import shlex


def agent_choices(client):
    """Select public display fields without copying instructions or credentials."""
    result = []
    for item in client.request("/api/agents")["items"]:
        document = item.get("document", {})
        document = document if isinstance(document, dict) else {}
        metadata, spec = document.get("metadata", {}), document.get("spec", {})
        malformed = not isinstance(metadata, dict) or not isinstance(spec, dict)
        metadata = metadata if isinstance(metadata, dict) else {}
        spec = spec if isinstance(spec, dict) else {}
        result.append(
            {
                "id": item["id"],
                "name": metadata.get("name")
                if isinstance(metadata.get("name"), str)
                else item["id"],
                "provider": spec.get("provider") or "codex",
                "invalid": bool(item.get("error")) or malformed,
            }
        )
    return result


def directed_message(text, client):
    """Parse the selector only; message JSON, quotes and newlines remain literal."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.get_token()  # The caller has already recognized /agent.
    selector = lexer.get_token()
    message = lexer.instream.read().strip()
    if not selector or not message:
        raise ValueError("Usage: /agent <id or quoted exact name> <message>")
    items = agent_choices(client)
    exact = [item for item in items if item["id"] == selector]
    matches = exact or [item for item in items if item["name"] == selector]
    if len(matches) != 1:
        raise ValueError("Choose one known agent using its ID or unique exact name")
    if matches[0]["invalid"]:
        raise ValueError("This agent definition is invalid; fix it in Studio first")
    return matches[0]["id"], message


def display_entrypoints(client):
    items = client.request("/api/catalog/entrypoints")["items"]
    for item in items:
        data = item.get("data", {})
        state = (
            "archived"
            if item.get("archived")
            else ("enabled in configuration" if data.get("enabled") else "disabled")
        )
        protocol = "HTTP / A2A" if data.get("a2a_enabled") else "HTTP"
        print(f"{item['id']} · {item.get('name', '')} · {protocol} · {state}")
    print(
        f"{len(items)} configured entrypoints. Connection and last-seen status are not tracked here."
    )
