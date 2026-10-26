"""Scoped Docker recovery requests shared by standalone and installed proxies.

No daemon listing is allowed without a known container created through that
proxy. Missing ownership must remain unconfirmed, never appear as empty success.
The module is standard-library-only for standalone deployment.
"""

import json
import re
from collections import OrderedDict
from urllib.parse import urlsplit

IDENTITY = re.compile(r"[a-f0-9]{64}\Z")
ANALYSIS_FILTER = re.compile(r"\^/(nexus-analysis-[a-f0-9]{32})\$\Z")
CONTAINER_PATH = re.compile(
    r"(?P<prefix>/v\d+(?:\.\d+)?)?/containers/[^/]+"
    r"(?P<action>/(?:attach|start|wait|kill|resize|json))?\Z"
)


class ProxyContainers:
    """Containers created through this proxy, by id and by name."""

    def __init__(self, max_tracked=512) -> None:
        self.max_tracked = max_tracked
        self._by_id: OrderedDict[str, tuple[str, list[str]]] = OrderedDict()
        self._retired: set[str] = set()

    def add(self, container_id: str, name: str, sources: list[str]) -> None:
        self._by_id[container_id] = (name, sources)
        while len(self._by_id) > self.max_tracked:
            oldest = next(
                (key for key in self._by_id if key in self._retired), next(iter(self._by_id))
            )
            self.remove(oldest)

    def resolve(self, ref: str) -> str | None:
        ref = ref.lstrip("/")
        if ref in self._by_id:
            return ref
        found = []
        for container_id, (name, _) in self._by_id.items():
            if (
                ref == name
                or ref == container_id
                or (len(ref) >= 12 and container_id.startswith(ref))
            ):
                found.append(container_id)
        return found[0] if len(found) == 1 else None

    def sources(self, container_id: str) -> list[str]:
        return self._by_id[container_id][1]

    def remove(self, container_id: str) -> None:
        self._by_id.pop(container_id, None)
        self._retired.discard(container_id)

    def retire(self, container_id: str) -> None:
        if container_id in self._by_id:
            self._retired.add(container_id)


def recovery_listing(query, resolve):
    """Replace an exact owned-name filter with both full identity and name."""
    if set(query) != {"all", "filters"} or query["all"] != ["1"]:
        raise ValueError("Only a scoped analysis cleanup lookup is allowed")
    raw = query["filters"]
    if (
        not isinstance(raw, list)
        or len(raw) != 1
        or not isinstance(raw[0], str)
        or len(raw[0]) > 4096
    ):
        raise ValueError("Invalid cleanup filter")
    filters = json.loads(raw[0])
    if not isinstance(filters, dict) or set(filters) != {"name"}:
        raise ValueError("Cleanup requires one exact analysis name")
    names = filters["name"]
    # Older Docker clients use boolean sets; current clients use string arrays.
    if isinstance(names, dict) and all(value is True for value in names.values()):
        names = list(names)
    if not isinstance(names, list) or len(names) != 1 or not isinstance(names[0], str):
        raise ValueError("Cleanup requires one exact analysis name")
    match = ANALYSIS_FILTER.fullmatch(names[0])
    if not match:
        raise ValueError("Cleanup requires one exact analysis name")
    identity = resolve(match[1])
    if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
        raise ValueError("Container ownership is unconfirmed")
    return {
        "all": ["1"],
        "filters": [
            json.dumps(
                {
                    "name": names,
                    "id": [identity],
                },
                separators=(",", ":"),
            )
        ],
    }


def container_target(target, identity):
    """Forward an authorized name/short-ID request using its full owned ID."""
    if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
        raise ValueError("Invalid owned container identity")
    parts = urlsplit(target)
    match = CONTAINER_PATH.fullmatch(parts.path)
    if not match:
        raise ValueError("Invalid container request path")
    return (
        (match["prefix"] or "")
        + "/containers/"
        + identity
        + (match["action"] or "")
        + ("?" + parts.query if parts.query else "")
    )
