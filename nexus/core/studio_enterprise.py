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

KINDS = {
    "bedrock",
    "bedrock-kb",
    "bedrock-flow",
    "mongodb",
    "nuclia",
    "powerbi",
    "neptune",
    "opensearch",
    "dynamodb",
    "elasticsearch",
    "neo4j",
}


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
    elif kind == "neptune":
        from nexus.core.studio_connections import https_url

        fields |= {
            "region",
            "access_key_env",
            "secret_key_env",
            "session_token_env",
            "endpoint",
            "allow_writes",
        }
        region = data.get("region", "")
        if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", region):
            raise ValueError("Choose the AWS region for this resource")
        if type(data.get("allow_writes", False)) is not bool:
            raise ValueError("Allow writes must be true or false")
        result.update(
            region=region,
            access_key_env=env_name(data.get("access_key_env", "AWS_ACCESS_KEY_ID")),
            secret_key_env=env_name(data.get("secret_key_env", "AWS_SECRET_ACCESS_KEY")),
            session_token_env=env_name(data["session_token_env"])
            if data.get("session_token_env")
            else "",
            endpoint=https_url(data.get("endpoint")),
            allow_writes=data.get("allow_writes", False),
        )
        supported = {"status", "query"} | ({"execute"} if result["allow_writes"] else set())
    elif kind == "dynamodb":
        fields |= {
            "region",
            "access_key_env",
            "secret_key_env",
            "session_token_env",
            "tables",
            "allow_writes",
        }
        region = data.get("region", "")
        if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", region):
            raise ValueError("Choose the AWS region for this resource")
        tables = data.get("tables")
        if not isinstance(tables, list) or not 1 <= len(tables) <= 50:
            raise ValueError("Select 1–50 DynamoDB table names")
        if type(data.get("allow_writes", False)) is not bool:
            raise ValueError("Allow writes must be true or false")
        result.update(
            region=region,
            access_key_env=env_name(data.get("access_key_env", "AWS_ACCESS_KEY_ID")),
            secret_key_env=env_name(data.get("secret_key_env", "AWS_SECRET_ACCESS_KEY")),
            session_token_env=env_name(data["session_token_env"])
            if data.get("session_token_env")
            else "",
            tables=[identifier(value, "DynamoDB table", 255) for value in tables],
            allow_writes=data.get("allow_writes", False),
        )
        supported = {"inspect_schema", "query"} | (
            {"execute"} if result["allow_writes"] else set()
        )
    elif kind in {"opensearch", "elasticsearch"}:
        fields |= {"url", "allow_private", "token_env", "indices", "allow_writes"}
        remote = validate_remote(
            {
                "url": data.get("url"),
                "allow_private": data.get("allow_private", False),
                "auth": {"type": "bearer", "token_env": env_name(data.get("token_env"))},
            }
        )
        indices = data.get("indices")
        if not isinstance(indices, list) or not 1 <= len(indices) <= 50:
            raise ValueError("Select 1–50 search index names")
        if type(data.get("allow_writes", False)) is not bool:
            raise ValueError("Allow writes must be true or false")
        cleaned_indices = [identifier(value, "Search index", 100) for value in indices]
        if any("/" in idx or ":" in idx for idx in cleaned_indices):
            raise ValueError("Search index names cannot contain path or colon separators")
        result.update(
            url=remote["url"],
            allow_private=remote["allow_private"],
            token_env=remote["auth"]["token_env"],
            indices=cleaned_indices,
            allow_writes=data.get("allow_writes", False),
        )
        supported = {"inspect_schema", "query"} | (
            {"execute"} if result["allow_writes"] else set()
        )
    elif kind == "neo4j":
        fields |= {"url", "allow_private", "token_env", "database", "allow_writes"}
        remote = validate_remote(
            {
                "url": data.get("url"),
                "allow_private": data.get("allow_private", False),
                "auth": {"type": "bearer", "token_env": env_name(data.get("token_env"))},
            }
        )
        database = identifier(data.get("database", "neo4j"), "Neo4j database", 63)
        if any(c in database for c in "/.:"):
            raise ValueError("Choose a single Neo4j database name")
        if type(data.get("allow_writes", False)) is not bool:
            raise ValueError("Allow writes must be true or false")
        result.update(
            url=remote["url"],
            allow_private=remote["allow_private"],
            token_env=remote["auth"]["token_env"],
            database=database,
            allow_writes=data.get("allow_writes", False),
        )
        supported = {"inspect_schema", "query"} | (
            {"execute"} if result["allow_writes"] else set()
        )
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
                token = arguments.get("next_token")
                if token is not None and (
                    not isinstance(token, str) or not 1 <= len(token) <= 2048
                ):
                    raise ValueError("Knowledge page token must be text up to 2048 characters")
                result = client.retrieve(
                    **({"nextToken": token} if token else {}),
                    knowledgeBaseId=data["resource_id"],
                    retrievalQuery={"text": query},
                    retrievalConfiguration={
                        "vectorSearchConfiguration": {"numberOfResults": limit}
                    },
                )
                items, next_token = result.get("retrievalResults", []), result.get("nextToken")
                if (
                    not isinstance(items, list)
                    or len(items) > limit
                    or (
                        next_token is not None
                        and (not isinstance(next_token, str) or len(next_token) > 2048)
                    )
                ):
                    raise ValueError("Knowledge provider returned an invalid bounded page")
                return bounded({"items": items, "untrusted": True, "next_token": next_token})
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
            paginate = arguments.get("paginate", "after_id" in arguments)
            if type(paginate) is not bool or "after_id" in arguments and not paginate:
                raise ValueError("Enable MongoDB pagination explicitly when supplying a cursor")
            if paginate:
                from bson import ObjectId

                after = arguments.get("after_id")
                if after is not None and (
                    not isinstance(after, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", after)
                ):
                    raise ValueError("MongoDB page cursor must be a 24-character ObjectId")
                if projection and projection.get("_id") == 0:
                    raise ValueError("MongoDB pagination requires _id in the projection")
                if after:
                    filters = {"$and": [filters, {"_id": {"$gt": ObjectId(after)}}]}
            cursor = (
                target.find(filters, projection)
                .max_time_ms(10000)
                .limit(limit + int(paginate))
                .batch_size(10)
            )
            if paginate:
                cursor = cursor.sort("_id", 1)
            try:
                items, size = [], 0
                for row in cursor:
                    size += len(json.dumps(row, default=str).encode())
                    if size > 256 * 1024:
                        raise ValueError("Connector response exceeds 256 KiB; narrow the query")
                    items.append(row)
                if paginate:
                    from bson import ObjectId

                    if any(not isinstance(row.get("_id"), ObjectId) for row in items):
                        raise ValueError("MongoDB pagination requires ObjectId document identities")
                    more = len(items) > limit
                    return bounded(
                        {
                            "items": items[:limit],
                            "truncated": more,
                            "next_after": str(items[limit - 1]["_id"]) if more else None,
                        }
                    )
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

    def neptune(self, data, identity, action, arguments):
        if "neptune" in self.factories:
            client = self.factories["neptune"](data, identity)
        else:
            try:
                import boto3
                from botocore.config import Config
            except ImportError:
                raise ValueError("Install nexus-arc[studio-enterprise] for Neptune") from None
            values = self.environment(identity)
            access, secret = values.get(data["access_key_env"]), values.get(data["secret_key_env"])
            if not access or not secret:
                raise PermissionError("Configure the selected account's AWS credential references")
            session = (
                values.get(data["session_token_env"]) if data["session_token_env"] else None
            )
            client = boto3.client(
                "neptunedata",
                region_name=data["region"],
                endpoint_url=data["endpoint"],
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                aws_session_token=session,
                config=Config(
                    connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1}
                ),
            )
        try:
            if action == "status":
                return bounded(client.get_engine_status())
            language = arguments.get("language", "openCypher")
            if language not in {"openCypher", "gremlin", "sparql"}:
                raise ValueError("Choose openCypher, gremlin or sparql")
            query = _text(arguments.get("query"), "Graph query", 8000)
            if action == "query":
                if re.search(
                    r"(?i)\b(create|merge|delete|detach|set|remove|drop|insert|addv|adde)\b",
                    query,
                ):
                    raise PermissionError("Graph mutations require an approved execute action")
            elif not data["allow_writes"] or action != "execute":
                raise PermissionError("Neptune writes are disabled")
            if language == "openCypher":
                response = client.execute_open_cypher_query(openCypherQuery=query)
            elif language == "gremlin":
                response = client.execute_gremlin_query(gremlinQuery=query)
            else:
                response = client.execute_sparql_query(sparql=query)
            return bounded({"results": response.get("results", response), "untrusted": True})
        finally:
            if hasattr(client, "close"):
                client.close()

    def dynamodb(self, data, identity, action, arguments):
        if "dynamodb" in self.factories:
            client = self.factories["dynamodb"](data, identity)
        else:
            try:
                import boto3
                from botocore.config import Config
            except ImportError:
                raise ValueError("Install nexus-arc[studio-enterprise] for DynamoDB") from None
            values = self.environment(identity)
            access, secret = values.get(data["access_key_env"]), values.get(data["secret_key_env"])
            if not access or not secret:
                raise PermissionError("Configure the selected account's AWS credential references")
            session = (
                values.get(data["session_token_env"]) if data["session_token_env"] else None
            )
            client = boto3.client(
                "dynamodb",
                region_name=data["region"],
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                aws_session_token=session,
                config=Config(
                    connect_timeout=5, read_timeout=15, retries={"total_max_attempts": 1}
                ),
            )
        try:
            if action == "inspect_schema":
                names = client.list_tables(Limit=100).get("TableNames", [])
                return bounded({"tables": [t for t in names if t in data["tables"]]})
            table = arguments.get("table")
            if table not in data["tables"]:
                raise PermissionError("Select a DynamoDB table enabled for this connection")
            if action == "query":
                limit = arguments.get("limit", 25)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise ValueError("DynamoDB query limit must be 1–100")
                condition = _text(arguments.get("key_condition"), "Key condition", 2000)
                values = arguments.get("expression_values", {})
                if not isinstance(values, dict) or not values:
                    raise ValueError("DynamoDB query requires expression_values")
                response = client.query(
                    TableName=table,
                    KeyConditionExpression=condition,
                    ExpressionAttributeValues=values,
                    Limit=limit,
                )
                return bounded(
                    {
                        "items": response.get("Items", []),
                        "next_key": response.get("LastEvaluatedKey"),
                    }
                )
            if not data["allow_writes"] or action != "execute":
                raise PermissionError("DynamoDB writes are disabled")
            operation = arguments.get("operation")
            if operation == "put_item":
                item = arguments.get("item")
                if not isinstance(item, dict) or not item:
                    raise ValueError("put_item requires a nonempty item object")
                client.put_item(TableName=table, Item=item)
                return {"written": True}
            if operation == "delete_item":
                key = arguments.get("key")
                if not isinstance(key, dict) or not key:
                    raise ValueError("delete_item requires a nonempty key object")
                client.delete_item(TableName=table, Key=key)
                return {"deleted": True}
            raise ValueError("Choose put_item or delete_item")
        finally:
            if hasattr(client, "close"):
                client.close()

    def call(self, data, identity, action, arguments):
        if data["type"].startswith("bedrock"):
            return self.bedrock(data, identity, arguments)
        if data["type"] == "mongodb":
            return self.mongo(data, identity, action, arguments)
        if data["type"] == "neptune":
            return self.neptune(data, identity, action, arguments)
        if data["type"] == "dynamodb":
            return self.dynamodb(data, identity, action, arguments)
        headers = remote_headers(
            self.studio,
            {**data, "auth": {"type": "bearer", "token_env": data["token_env"]}},
            identity,
        )
        if data["type"] == "nuclia":
            headers = {"X-NUCLIA-SERVICEACCOUNT": headers["Authorization"]}
            url = data["url"] + "/api/v1/kb/" + data["knowledge_box"] + "/find"
            top_k = arguments.get("top_k", 10)
            page = arguments.get("page", 0)
            if (
                type(top_k) is not int
                or not 1 <= top_k <= 50
                or type(page) is not int
                or not 0 <= page <= 100
            ):
                raise ValueError("Nuclia top_k must be 1–50 and page 0–100")
            body = {
                "query": _text(arguments.get("query"), "Search query", 4000),
                "top_k": top_k,
                **({"page_number": page} if page else {}),
            }
            method = "POST"
        elif data["type"] in {"opensearch", "elasticsearch"}:
            if action == "inspect_schema":
                method, url, body = "GET", data["url"] + "/_cat/indices?format=json", None
            else:
                index = arguments.get("index")
                if index not in data["indices"]:
                    raise PermissionError("Select a search index enabled for this connection")
                if action == "query":
                    size = arguments.get("size", 20)
                    if type(size) is not int or not 1 <= size <= 100:
                        raise ValueError("Search size must be 1–100")
                    query = arguments.get("query", {"match_all": {}})
                    if not isinstance(query, dict) or "script" in json.dumps(query):
                        raise ValueError("Search query must be a script-free DSL object")
                    method, url, body = (
                        "POST",
                        f"{data['url']}/{index}/_search",
                        {"query": query, "size": size},
                    )
                elif data["allow_writes"] and action == "execute":
                    doc = arguments.get("document")
                    if not isinstance(doc, dict) or not doc:
                        raise ValueError("Search indexing requires a nonempty document")
                    method, url, body = "POST", f"{data['url']}/{index}/_doc", doc
                else:
                    raise PermissionError("Search index writes are disabled")
        elif data["type"] == "neo4j":
            url = f"{data['url']}/db/{data['database']}/tx/commit"
            method = "POST"
            if action == "inspect_schema":
                body = {
                    "statements": [
                        {"statement": "CALL db.labels() YIELD label RETURN label LIMIT 100"}
                    ]
                }
            else:
                statement = _text(
                    arguments.get("statement") or arguments.get("query"),
                    "Cypher statement",
                    8000,
                )
                params = arguments.get("parameters", {})
                if not isinstance(params, dict):
                    raise ValueError("Neo4j parameters must be an object")
                if action == "query":
                    if re.search(
                        r"(?i)\b(create|merge|delete|detach|set|remove|drop)\b", statement
                    ):
                        raise PermissionError("Neo4j mutations require an approved execute action")
                elif not data["allow_writes"] or action != "execute":
                    raise PermissionError("Neo4j writes are disabled")
                body = {"statements": [{"statement": statement, "parameters": params}]}
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
