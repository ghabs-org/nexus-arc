"""Explicit legacy memory migration with server-owned identity/source mappings."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from nexus.core.studio_catalog import _text


class StudioMemorySources:
    def __init__(self, workbench, sources=None, redis_factory=None):
        self.workbench = workbench
        self.studio = workbench.studio
        self.sources = sources or {}
        self.redis_factory = redis_factory
        if not isinstance(self.sources, dict):
            raise ValueError("Memory sources must be an administrator-owned mapping")
        for identity, source in self.sources.items():
            if (
                not isinstance(identity, str)
                or not isinstance(source, dict)
                or source.get("kind") not in {"filesystem", "redis"}
            ):
                raise ValueError("Memory sources support filesystem and Redis")
            from nexus.core.studio_connections import env_name

            env_name(source.get("path_env" if source["kind"] == "filesystem" else "url_env"))
            mapping = source.get("identities", {})
            if not isinstance(mapping, dict) or any(
                not str(value).isdecimal() for value in mapping.values()
            ):
                raise ValueError("Map Nexus identities explicitly to legacy numeric user IDs")

    def source(self, identity, actor):
        principal = self.workbench.owner(actor) or "desktop"
        source = self.sources.get(identity)
        if not source or principal not in source.get("identities", {}):
            raise PermissionError("No legacy identity mapping is configured for your account")
        return source, str(source["identities"][principal])

    def list_sources(self, actor):
        principal = self.workbench.owner(actor) or "desktop"
        return [
            {"id": identity, "name": str(source.get("name") or identity), "kind": source["kind"]}
            for identity, source in self.sources.items()
            if principal in source.get("identities", {})
        ]

    def redis(self, source):
        url = os.getenv(source.get("url_env", ""))
        if not url:
            raise ValueError("Legacy Redis connection environment reference is not configured")
        if self.redis_factory:
            return self.redis_factory(url)
        import redis

        return redis.Redis.from_url(
            url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3
        )

    def filesystem(self, source, user):
        path = os.getenv(source.get("path_env", ""))
        if not path:
            raise ValueError("Legacy memory file environment reference is not configured")
        file = Path(path)
        if file.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Legacy metadata file exceeds the migration limit")
        return json.loads(file.read_text()).get("users", {}).get(user, {}).get("chats", {})

    def chats(self, identity, actor):
        source, user = self.source(identity, actor)
        if source["kind"] == "filesystem":
            chats = self.filesystem(source, user)
        else:
            client = self.redis(source)
            try:
                chats = {
                    key: json.loads(value)
                    for key, value in client.hgetall("user_chats:" + user).items()
                }
            finally:
                client.close()
        return [
            {"id": str(key), "title": str(value.get("title") or key)[:200]}
            for key, value in list(chats.items())[:1000]
        ]

    def import_chat(self, identity, chat_id, actor):
        source, user = self.source(identity, actor)
        if source["kind"] == "filesystem":
            chat = self.filesystem(source, user).get(chat_id)
            if not chat:
                raise PermissionError("Chat does not belong to your mapped legacy identity")
            messages = chat.get("messages", [])
        else:
            client = self.redis(source)
            try:
                metadata = client.hget("user_chats:" + user, chat_id)
                if not metadata:
                    raise PermissionError("Chat does not belong to your mapped legacy identity")
                chat = json.loads(metadata)
                messages = [
                    json.loads(value)
                    for value in client.lrange("chat_history:" + chat_id, -1000, -1)
                ]
            finally:
                client.close()
        if not messages:
            raise ValueError(
                "No persisted transcript is available. ARC filesystem metadata alone does not contain ephemeral chat history"
            )
        return self.import_document(
            {
                "format": "nexus-arc-memory/v1",
                "conversation": {"title": chat.get("title") or chat_id, "messages": messages},
            },
            actor,
            provenance=identity + ":" + chat_id,
        )

    def import_document(self, document, actor, provenance="uploaded-export"):
        owner = self.workbench.owner(actor)
        if not isinstance(document, dict) or document.get("format") not in {
            "nexus-studio-memory/v1",
            "nexus-arc-memory/v1",
        }:
            raise ValueError("Choose a Nexus Studio or Nexus ARC memory export")
        if len(json.dumps(document).encode()) > 900 * 1024:
            raise ValueError("Memory export must be no larger than 900 KiB")
        conversation = document.get("conversation", {})
        if not isinstance(conversation, dict):
            raise ValueError("Memory export conversation must be an object")
        title = _text(conversation.get("title"), "Conversation title", 200)
        entries = conversation.get("messages")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ValueError("Import between one and one thousand messages")
        messages = []
        for item in entries:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                raise ValueError("Imported messages may only use user and assistant roles")
            text = _text(item.get("content", item.get("text")), "Message content", 131072)
            messages.append({"role": item["role"], "content": text})
        digest = hashlib.sha256(
            json.dumps([owner, provenance, title, messages], sort_keys=True).encode()
        ).hexdigest()
        identity = "import-" + digest[:32]
        path = self.studio._path("sessions", identity)
        with self.studio.lock:
            if path.exists():
                return {"id": identity, "already_imported": True}
            from nexus.core.studio_workbench import now

            self.studio._write(
                path,
                {
                    "id": identity,
                    "owner_id": owner,
                    "title": title,
                    "messages": messages,
                    "created_at": now(),
                    "updated_at": now(),
                    "provider": "imported-history",
                    "archived": False,
                    "import": {
                        "source": provenance,
                        "digest": digest,
                        "format": document["format"],
                    },
                },
            )
        return {"id": identity, "already_imported": False}
