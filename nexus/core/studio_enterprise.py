"""Optional official-catalog equivalents behind existing connection permissions.

Credentials are resolved per Nexus identity. Reads are bounded; Bedrock agent /
flow invocations and MongoDB writes require the connection's reviewed action
path. SDK retries are disabled so an uncertain mutation is never replayed.
"""

import hashlib
import json
import os
import re
import time
import uuid

from nexus.core.studio_catalog import _text
from nexus.core.studio_remote import remote_headers, validate_remote

KINDS = {"bedrock", "bedrock-kb", "bedrock-flow", "mongodb", "nuclia", "powerbi"}


def identifier(value, label, maximum=200):
    value = _text(value, label, maximum)
    if not re.fullmatch(r"[A-Za-z0-9_:/.-]+", value) or ".." in value:
        raise ValueError(f"{label} must be a resource identifier")
    return value


def validate_enterprise(data):
    from nexus.core.studio_connections import env_name

    kind = data["type"]
    if type(data.get("enabled", False)) is not bool:
        raise ValueError("Enabled must be true or false")
    result = {"type": kind, "enabled": data.get("enabled", False)}
    supported = {"query"}
    fields = {"type", "enabled", "actions"}
    if kind.startswith("bedrock"):
        fields |= {
            "region",
            "access_key_env",
            "secret_key_env",
            "session_token_env",
            "resource_id",
            "alias_id",
            "input_node",
            "input_name",
        }
        region = data.get("region", "")
        if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", region):
            raise ValueError("Choose the AWS region for this resource")
        result.update(
            region=region,
            access_key_env=env_name(data.get("access_key_env", "AWS_ACCESS_KEY_ID")),
            secret_key_env=env_name(data.get("secret_key_env", "AWS_SECRET_ACCESS_KEY")),
            session_token_env=env_name(data["session_token_env"])
            if data.get("session_token_env")
            else "",
            resource_id=identifier(data.get("resource_id"), "Bedrock resource ID", 512),
        )
        if kind != "bedrock-kb":
            result["alias_id"] = identifier(data.get("alias_id"), "Bedrock alias", 512)
            supported = {"invoke"}
        if kind == "bedrock-flow":
            result["input_node"] = identifier(data.get("input_node"), "Flow input node")
            result["input_name"] = identifier(data.get("input_name", "document"), "Flow input name")
    elif kind == "mongodb":
        fields |= {"dsn_env", "database", "collections", "allow_writes"}
        result["dsn_env"] = env_name(data.get("dsn_env"))
        result["database"] = identifier(data.get("database"), "MongoDB database", 63)
        if any(c in result["database"] for c in "/.:"):
            raise ValueError("Choose a single MongoDB database name")
        collections = data.get("collections")
        if not isinstance(collections, list) or not 1 <= len(collections) <= 50:
            raise ValueError("Select 1–50 MongoDB collection names")
        result["collections"] = [identifier(value, "Collection name", 100) for value in collections]
        if type(data.get("allow_writes", False)) is not bool:
            raise ValueError("Allow writes must be true or false")
        result["allow_writes"] = data.get("allow_writes", False)
        supported = {"query", "inspect_schema"} | ({"execute"} if result["allow_writes"] else set())
    elif kind == "nuclia":
        fields |= {"url", "allow_private", "token_env", "knowledge_box"}
        remote = validate_remote(
            {
                "url": data.get("url"),
                "allow_private": data.get("allow_private", False),
                "auth": {"type": "bearer", "token_env": env_name(data.get("token_env"))},
            }
        )
        result.update(
            url=remote["url"],
            allow_private=remote["allow_private"],
            token_env=remote["auth"]["token_env"],
        )
        result["knowledge_box"] = identifier(data.get("knowledge_box"), "Knowledge box ID")
        if "/" in result["knowledge_box"]:
            raise ValueError("Choose a knowledge box ID without a path")
    elif kind == "powerbi":
        fields |= {"token_env", "dataset_id", "workspace_id"}
        result["token_env"] = env_name(data.get("token_env"))
        result["dataset_id"] = str(uuid.UUID(str(data.get("dataset_id", ""))))
        result["workspace_id"] = (
            str(uuid.UUID(data["workspace_id"])) if data.get("workspace_id") else ""
        )
        supported = {"read", "query"}
    else:
        raise ValueError("Unknown catalog connector")
    if set(data) - fields:
        raise ValueError("Unsupported connector fields: " + ", ".join(sorted(set(data) - fields)))
    actions = data.get("actions", sorted(supported - {"execute"}))
    if not isinstance(actions, list) or set(actions) - supported:
        raise ValueError("Choose supported connector actions: " + ", ".join(sorted(supported)))
    result["actions"] = list(dict.fromkeys(actions))
    return result


def bounded(value):
    raw = json.dumps(value, default=str, ensure_ascii=False)
    if len(raw.encode()) > 256 * 1024:
        raise ValueError("Connector response exceeds 256 KiB; narrow the query")
    return json.loads(raw)


class StudioEnterprise:
    def __init__(self, studio, client_factories=None, request_client=None):
        self.studio = studio
        self.factories = client_factories or {}
        self.request_client = request_client
        self.mongo_clients = {}

    def close(self):
        for client in self.mongo_clients.values():
            client.close()
        self.mongo_clients.clear()

    def environment(self, identity):
        if not self.studio.team:
            return os.environ
        self.studio.team.assert_execution(identity)
        values, error = self.studio.team.environment_resolver(identity)
        if error:
            raise PermissionError("Connector account credentials are unavailable")
        return values

    def aws(self, data, identity):
        if "bedrock" in self.factories:
            return self.factories["bedrock"](data, identity)
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise ValueError("Install nexus-arc[studio-enterprise] for Bedrock") from None
        values = self.environment(identity)
        access, secret = values.get(data["access_key_env"]), values.get(data["secret_key_env"])
        if not access or not secret:
            raise PermissionError("Configure the selected account's AWS credential references")
        session = values.get(data["session_token_env"]) if data["session_token_env"] else None
        if data["session_token_env"] and not session:
            raise PermissionError("Configure the AWS session token reference")
        return boto3.client(
            "bedrock-agent-runtime",
            region_name=data["region"],
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            aws_session_token=session,
            config=Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1}),
        )

    def bedrock(self, data, identity, arguments):
        client = self.aws(data, identity)
        try:
            if data["type"] == "bedrock-kb":
                query = _text(arguments.get("query"), "Knowledge query", 4000)
                limit = arguments.get("limit", 5)
                if type(limit) is not int or not 1 <= limit <= 20:
                    raise ValueError("Retrieve 1–20 results")
                result = client.retrieve(
                    knowledgeBaseId=data["resource_id"],
                    retrievalQuery={"text": query},
                    retrievalConfiguration={
                        "vectorSearchConfiguration": {"numberOfResults": limit}
                    },
                )
                return bounded({"items": result.get("retrievalResults", []), "untrusted": True})
            if data["type"] == "bedrock":
                session = arguments.get("session_id") or uuid.uuid4().hex
                if not isinstance(session, str) or not re.fullmatch(
                    r"[A-Za-z0-9._:-]{2,100}", session
                ):
                    raise ValueError("Bedrock session ID must be 2–100 identifier characters")
                response = client.invoke_agent(
                    agentId=data["resource_id"],
                    agentAliasId=data["alias_id"],
                    sessionId=session,
                    inputText=_text(arguments.get("text"), "Agent input", 16000),
                    enableTrace=False,
                )
                stream = response["completion"]
            else:
                if "input" not in arguments:
                    raise ValueError("Bedrock flow requires a structured input value")
                response = client.invoke_flow(
                    flowIdentifier=data["resource_id"],
                    flowAliasIdentifier=data["alias_id"],
                    inputs=[
                        {
                            "nodeName": data["input_node"],
                            "nodeOutputName": data["input_name"],
                            "content": {"document": arguments["input"]},
                        }
                    ],
                )
                stream, session = response["responseStream"], None
            events, text, size, start = [], [], 0, time.monotonic()
            try:
                for index, event in enumerate(stream):
                    if index >= 200 or time.monotonic() - start > 90:
                        raise ValueError(
                            "Bedrock stream exceeded execution limits; review external state"
                        )
                    chunk = event.get("chunk", {})
                    value = chunk.get("bytes", b"").decode(errors="replace")
                    text.append(value)
                    safe = {k: v for k, v in event.items() if k != "chunk"}
                    if chunk.get("attribution"):
                        safe["attribution"] = chunk["attribution"]
                    size += len(value.encode()) + len(json.dumps(safe, default=str).encode())
                    if size > 256 * 1024:
                        raise ValueError("Bedrock result exceeds 256 KiB")
                    if any(k.endswith("Exception") for k in safe):
                        raise ValueError(
                            "Bedrock reported an execution error; review external state"
                        )
                    if safe:
                        events.append(safe)
            finally:
                if hasattr(stream, "close"):
                    stream.close()
            return {"output": "".join(text), "events": events, "session_id": session}
        finally:
            if hasattr(client, "close"):
                client.close()

    def mongo(self, data, identity, action, arguments):
        values = self.environment(identity)
        dsn = values.get(data["dsn_env"])
        if not dsn or not dsn.startswith(("mongodb://", "mongodb+srv://")):
            raise PermissionError("Configure the MongoDB URI environment reference")
        key = hashlib.sha256(json.dumps([identity, data, dsn], sort_keys=True).encode()).hexdigest()
        with self.studio.lock:
            if key not in self.mongo_clients:
                if len(self.mongo_clients) >= 32:
                    _, old = self.mongo_clients.popitem()
                    old.close()
                if "mongodb" in self.factories:
                    client = self.factories["mongodb"](dsn)
                else:
                    try:
                        import pymongo
                    except ImportError:
                        raise ValueError(
                            "Install nexus-arc[studio-enterprise] for MongoDB"
                        ) from None
                    client = pymongo.MongoClient(
                        dsn,
                        maxPoolSize=4,
                        serverSelectionTimeoutMS=5000,
                        connectTimeoutMS=5000,
                        timeoutMS=15000,
                        retryReads=False,
                        retryWrites=False,
                    )
                self.mongo_clients[key] = client
            db = self.mongo_clients[key][data["database"]]
        if action == "inspect_schema":
            return {
                "collections": [
                    name for name in db.list_collection_names() if name in data["collections"]
                ]
            }
        collection = arguments.get("collection")
        if collection not in data["collections"]:
            raise PermissionError("Select a collection enabled for this connection")
        filters = arguments.get("filter", {})
        if not isinstance(filters, dict):
            raise ValueError("MongoDB filter must be an object")

        def check(value, depth=0):
            if depth > 20:
                raise ValueError("MongoDB filter exceeds nesting limit")
            if isinstance(value, dict):
                if set(value) & {"$where", "$function", "$accumulator", "$out", "$merge"}:
                    raise ValueError("MongoDB server code and output stages are disabled")
                for v in value.values():
                    check(v, depth + 1)
            elif isinstance(value, list):
                for v in value:
                    check(v, depth + 1)

        check(filters)
        target = db[collection]
        if action == "query":
            limit = arguments.get("limit", 100)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("MongoDB query limit must be 1–100")
            projection = arguments.get("projection")
            if projection is not None and (
                not isinstance(projection, dict)
                or any(type(v) is not int or v not in {0, 1} for v in projection.values())
            ):
                raise ValueError("Projection maps field names to 0 or 1")
            cursor = target.find(filters, projection).max_time_ms(10000).limit(limit).batch_size(10)
            try:
                items, size = [], 0
                for row in cursor:
                    size += len(json.dumps(row, default=str).encode())
                    if size > 256 * 1024:
                        raise ValueError("Connector response exceeds 256 KiB; narrow the query")
                    items.append(row)
                return bounded({"items": items})
            finally:
                cursor.close()
        if not data["allow_writes"] or action != "execute":
            raise PermissionError("MongoDB writes are disabled")
        operation = arguments.get("operation")
        if operation == "insert_one":
            document = arguments.get("document")
            if not isinstance(document, dict) or not document:
                raise ValueError("Insert requires a nonempty document")
            result = target.insert_one(document)
            return {"inserted_id": str(result.inserted_id)}
        if operation == "update_one":
            update = arguments.get("update")
            if (
                not filters
                or not isinstance(update, dict)
                or set(update) - {"$set", "$unset", "$inc"}
                or not update
            ):
                raise ValueError("Update requires a filter and $set, $unset or $inc object")
            check(update)
            result = target.update_one(filters, update, upsert=False)
            return {"matched": result.matched_count, "modified": result.modified_count}
        if operation == "delete_one" and filters:
            return {"deleted": target.delete_one(filters).deleted_count}
        raise ValueError("Choose insert_one, update_one or filtered delete_one")

    def call(self, data, identity, action, arguments):
        if data["type"].startswith("bedrock"):
            return self.bedrock(data, identity, arguments)
        if data["type"] == "mongodb":
            return self.mongo(data, identity, action, arguments)
        headers = remote_headers(
            self.studio,
            {**data, "auth": {"type": "bearer", "token_env": data["token_env"]}},
            identity,
        )
        if data["type"] == "nuclia":
            headers = {"X-NUCLIA-SERVICEACCOUNT": headers["Authorization"]}
            url = data["url"] + "/api/v1/kb/" + data["knowledge_box"] + "/find"
            method, body = (
                "POST",
                {"query": _text(arguments.get("query"), "Search query", 4000), "top_k": 10},
            )
        else:
            url = "https://api.powerbi.com/v1.0/myorg/"
            if data["workspace_id"]:
                url += "groups/" + data["workspace_id"] + "/"
            url += "datasets/" + data["dataset_id"]
            method, body = "GET", None
            if action == "query":
                method, url = "POST", url + "/executeQueries"
                body = {
                    "queries": [{"query": _text(arguments.get("query"), "DAX query", 16000)}],
                    "serializerSettings": {"includeNulls": True},
                }
        if self.request_client:
            return bounded(self.request_client(method, url, headers, body))
        return bounded(
            self.studio.research.request(
                method, url, headers, body, private=data.get("allow_private", False)
            )
        )
