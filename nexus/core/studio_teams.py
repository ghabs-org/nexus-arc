"""Selected Teams channel operations through identity-scoped Microsoft Graph tokens."""

import re
import uuid
from urllib.parse import parse_qs, quote, unquote, urlparse

from nexus.core.studio_remote import remote_headers

GRAPH = "https://graph.microsoft.com/v1.0"
ACTIONS = {"status", "query", "send", "update_message"}


def segment(value, label):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_:@.~-]{1,200}", value)
        or value in {".", ".."}
    ):
        raise ValueError(f"Choose a bounded {label} without path separators")
    return quote(value, safe="")


def validate_teams(data):
    from nexus.core.studio_connections import env_name

    if set(data) - {"type", "team_id", "channel_id", "token_env", "actions", "enabled"}:
        raise ValueError("Teams uses a selected team/channel and credential reference only")
    if type(data.get("enabled", False)) is not bool:
        raise ValueError("Enabled must be true or false")
    team = str(uuid.UUID(str(data.get("team_id", ""))))
    segment(data.get("channel_id"), "channel ID")
    actions = data.get("actions", ["status", "query"])
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(a, str) or a not in ACTIONS for a in actions)
    ):
        raise ValueError("Choose status, query, send or update_message Teams actions")
    return {
        "type": "teams",
        "team_id": team,
        "channel_id": data["channel_id"],
        "token_env": env_name(data.get("token_env")),
        "enabled": data.get("enabled", False),
        "actions": list(dict.fromkeys(actions)),
    }


def call_teams(studio, data, identity, action, arguments, request_client=None):
    from nexus.core.studio_enterprise import bounded

    if action not in data["actions"] or action not in ACTIONS:
        raise PermissionError("This Teams action is not enabled")
    if not isinstance(arguments, dict):
        raise ValueError("Teams arguments must be an object")
    allowed = {
        "status": set(),
        "query": {"limit", "next_page", "message_id", "replies"},
        "send": {"text", "reply_to"},
        "update_message": {"text", "message_id"},
    }[action]
    if set(arguments) - allowed:
        raise ValueError("Unknown Teams operation arguments")
    headers = remote_headers(
        studio, {"auth": {"type": "bearer", "token_env": data["token_env"]}}, identity
    )
    base = (
        GRAPH
        + "/teams/"
        + data["team_id"]
        + "/channels/"
        + segment(data["channel_id"], "channel ID")
    )
    method, body, url = "GET", None, base
    if action == "status":
        url += "?$select=id,displayName"
    elif action == "query":
        limit = arguments.get("limit", 20)
        if (
            type(limit) is not int
            or not 1 <= limit <= 50
            or type(arguments.get("replies", False)) is not bool
        ):
            raise ValueError("Teams reads require a 1–50 page size and boolean replies")
        url += "/messages"
        if arguments.get("message_id"):
            url += "/" + segment(arguments["message_id"], "message ID")
            if arguments.get("replies"):
                url += "/replies"
        elif arguments.get("replies"):
            raise ValueError("Choose a message before reading replies")
        if arguments.get("next_page"):
            target = arguments["next_page"]
            if not isinstance(target, str) or len(target) > 16000:
                raise ValueError("Invalid Teams page cursor")
            parsed, expected = urlparse(target), urlparse(url)
            if (
                (parsed.scheme, parsed.netloc, unquote(parsed.path))
                != (expected.scheme, expected.netloc, unquote(expected.path))
                or parsed.fragment
                or set(parse_qs(parsed.query)) - {"$skiptoken", "$top"}
            ):
                raise ValueError("Teams page cursor must remain on the selected channel operation")
            page = parse_qs(parsed.query)
            if any(len(values) != 1 for values in page.values()) or (
                "$top" in page
                and (not page["$top"][0].isdigit() or not 1 <= int(page["$top"][0]) <= 50)
            ):
                raise ValueError("Invalid Teams page size or repeated cursor parameter")
            url = target
        elif not arguments.get("message_id") or arguments.get("replies"):
            url += "?$top=" + str(limit)
    else:
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16000:
            raise ValueError("Teams message text must be nonempty and at most 16000 bytes")
        body = {"body": {"contentType": "text", "content": text}}
        url += "/messages"
        if action == "send":
            method = "POST"
            if arguments.get("reply_to"):
                url += "/" + segment(arguments["reply_to"], "parent message ID") + "/replies"
        else:
            method = "PATCH"
            url += "/" + segment(arguments.get("message_id"), "message ID")
    try:
        result = (
            request_client(method, url, headers, body)
            if request_client
            else studio.research.request(method, url, headers, body)
        )
        return bounded(result)
    except Exception:
        # An uncertain POST/PATCH is never retried; do not expose provider bodies/tokens.
        raise ValueError(
            "Teams operation failed; verify delegated account permissions and remote message state before retrying"
        ) from None
