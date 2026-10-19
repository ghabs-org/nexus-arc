import asyncio

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_enterprise import validate_enterprise


def test_bedrock_knowledge_agent_and_flow_contracts_and_review_gate(tmp_path):
    calls = []

    class Client:
        def retrieve(self, **kwargs):
            calls.append(("retrieve", kwargs))
            return {
                "retrievalResults": [
                    {
                        "content": {"text": "Verified knowledge"},
                        "location": {"s3Location": {"uri": "s3://fixture/policy"}},
                    }
                ]
            }

        def invoke_agent(self, **kwargs):
            calls.append(("invoke_agent", kwargs))
            return {"completion": iter([{"chunk": {"bytes": b"Agent answer"}}])}

        def invoke_flow(self, **kwargs):
            calls.append(("invoke_flow", kwargs))
            return {
                "responseStream": iter(
                    [{"flowOutputEvent": {"content": {"document": {"answer": 42}}}}]
                )
            }

    studio = StudioService(
        tmp_path,
        connection_options={
            "enterprise_options": {"client_factories": {"bedrock": lambda data, identity: Client()}}
        },
    )
    try:
        for kind, arguments in [
            ("bedrock-kb", {"query": "policy"}),
            ("bedrock", {"text": "Help"}),
            ("bedrock-flow", {"input": {"task": "Help"}}),
        ]:
            data = {
                "type": kind,
                "region": "eu-west-1",
                "resource_id": "ABCDEFGHIJ",
                "enabled": True,
            }
            if kind != "bedrock-kb":
                data["alias_id"] = "ABCDEFGHIJ"
            if kind == "bedrock-flow":
                data["input_node"] = "FlowInputNode"
            assert validate_enterprise(validate_enterprise(data)) == validate_enterprise(data)
            record = studio.catalog.save("connectors", {"name": kind, "data": data})
            action = "query" if kind == "bedrock-kb" else "invoke"
            if action == "invoke":
                count = len(calls)
                with pytest.raises(ValueError, match="confirm"):
                    asyncio.run(
                        studio.connections.execute(
                            record["id"], {"action": action, "arguments": arguments}
                        )
                    )
                assert len(calls) == count
            result = asyncio.run(
                studio.connections.execute(
                    record["id"], {"action": action, "arguments": arguments, "confirm": True}
                )
            )
            assert result
        assert [c[0] for c in calls] == ["retrieve", "invoke_agent", "invoke_flow"]
        assert (
            calls[0][1]["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"]
            == 5
        )
        assert calls[2][1]["inputs"][0]["content"]["document"] == {"task": "Help"}
    finally:
        studio.close()


def test_powerbi_and_nuclia_requests_are_scoped_and_do_not_store_secrets(tmp_path, monkeypatch):
    requests = []

    def request(method, url, headers, body):
        requests.append((method, url, headers, body))
        return {"results": [{"answer": "contract response"}]}

    monkeypatch.setenv("FIXTURE_SERVICE_TOKEN", "private-fixture-token")
    studio = StudioService(
        tmp_path, connection_options={"enterprise_options": {"request_client": request}}
    )
    configs = [
        {
            "type": "nuclia",
            "url": "https://europe-1.nuclia.cloud",
            "knowledge_box": "kb-fixture",
            "token_env": "FIXTURE_SERVICE_TOKEN",
            "enabled": True,
        },
        {
            "type": "powerbi",
            "dataset_id": "cfafbeb1-8037-4d0c-896e-a46fb27ff229",
            "token_env": "FIXTURE_SERVICE_TOKEN",
            "enabled": True,
        },
    ]
    try:
        for data in configs:
            assert validate_enterprise(validate_enterprise(data)) == validate_enterprise(data)
            record = studio.catalog.save("connectors", {"name": data["type"], "data": data})
            assert "private-fixture-token" not in str(record)
            result = asyncio.run(
                studio.connections.execute(
                    record["id"],
                    {"action": "query", "arguments": {"query": 'EVALUATE ROW("x", 1)'}},
                )
            )
            assert result["results"]
        assert requests[0][1].endswith("/api/v1/kb/kb-fixture/find")
        assert requests[0][2] == {"X-NUCLIA-SERVICEACCOUNT": "Bearer private-fixture-token"}
        assert requests[1][1].endswith(
            "/datasets/cfafbeb1-8037-4d0c-896e-a46fb27ff229/executeQueries"
        )
        assert "impersonatedUserName" not in requests[1][3]
    finally:
        studio.close()


def test_mongo_pool_readonly_scope_filter_and_write_confirmation(tmp_path, monkeypatch):
    calls = []

    class Cursor:
        def max_time_ms(self, value):
            assert value == 10000
            return self

        def limit(self, value):
            return self

        def batch_size(self, value):
            return self

        def __iter__(self):
            return iter([{"name": "Fixture"}])

        def close(self):
            pass

    class Collection:
        def find(self, filters, projection):
            calls.append(("find", filters))
            return Cursor()

        def delete_one(self, filters):
            calls.append(("delete", filters))
            return type("Result", (), {"deleted_count": 1})()

    class Database:
        def __getitem__(self, name):
            assert name == "policies"
            return Collection()

        def list_collection_names(self):
            return ["policies", "private"]

    class Client:
        def __getitem__(self, name):
            assert name == "knowledge"
            return Database()

        def close(self):
            pass

    monkeypatch.setenv("FIXTURE_MONGO_DSN", "mongodb://fixture.invalid")
    studio = StudioService(
        tmp_path,
        connection_options={
            "enterprise_options": {"client_factories": {"mongodb": lambda uri: Client()}}
        },
    )
    try:
        record = studio.catalog.save(
            "connectors",
            {
                "name": "Mongo knowledge",
                "data": {
                    "type": "mongodb",
                    "database": "knowledge",
                    "collections": ["policies"],
                    "dsn_env": "FIXTURE_MONGO_DSN",
                    "allow_writes": True,
                    "enabled": True,
                    "actions": ["query", "inspect_schema", "execute"],
                },
            },
        )

        async def execute(action, arguments, **extra):
            return await studio.connections.execute(
                record["id"], {"action": action, "arguments": arguments, **extra}
            )

        assert (
            asyncio.run(execute("query", {"collection": "policies"}))["items"][0]["name"]
            == "Fixture"
        )
        assert asyncio.run(execute("inspect_schema", {}))["collections"] == ["policies"]
        for arguments in (
            {"collection": "private"},
            {"collection": "policies", "filter": {"$where": "code"}},
        ):
            with pytest.raises((ValueError, PermissionError)):
                asyncio.run(execute("query", arguments))
        with pytest.raises(ValueError, match="confirm"):
            asyncio.run(
                execute(
                    "execute",
                    {"collection": "policies", "operation": "delete_one", "filter": {"id": 1}},
                )
            )
        assert (
            asyncio.run(
                execute(
                    "execute",
                    {"collection": "policies", "operation": "delete_one", "filter": {"id": 1}},
                    confirm=True,
                )
            )["deleted"]
            == 1
        )
        assert [r[0] for r in calls] == ["find", "delete"] and len(
            studio.connections.enterprise.mongo_clients
        ) == 1
    finally:
        studio.close()
