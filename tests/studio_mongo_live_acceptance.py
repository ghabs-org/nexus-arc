"""Explicit live MongoDB pagination and reviewed-write contract; no silent skips."""

import asyncio
import os

import pymongo
import pytest

from nexus.core.studio import StudioService


def test_real_mongodb_pages_preserve_filter_collection_and_write_approval(tmp_path, monkeypatch):
    uri = os.environ.get("NEXUS_MONGO_LIVE_DSN")
    assert uri, "Set an isolated disposable NEXUS_MONGO_LIVE_DSN"
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=3000)
    target = client.nexus_acceptance.rows
    target.insert_many(
        [{"group": "selected", "index": i} for i in range(5)] + [{"group": "other", "index": 9}]
    )
    monkeypatch.setenv("LIVE_MONGO_DSN", uri)
    studio = StudioService(tmp_path)
    try:
        connector = studio.catalog.save(
            "connectors",
            {
                "name": "Live MongoDB",
                "data": {
                    "type": "mongodb",
                    "database": "nexus_acceptance",
                    "collections": ["rows"],
                    "dsn_env": "LIVE_MONGO_DSN",
                    "enabled": True,
                    "allow_writes": True,
                    "actions": ["query", "inspect_schema", "execute"],
                },
            },
        )

        def call(action, arguments, confirm=False):
            return asyncio.run(
                studio.connections.execute(
                    connector["id"], {"action": action, "arguments": arguments, "confirm": confirm}
                )
            )

        ids, cursor = [], None
        for index in range(3):
            arguments = {
                "collection": "rows",
                "filter": {"group": "selected"},
                "paginate": True,
                "limit": 2,
            }
            if cursor:
                arguments["after_id"] = cursor
            page = call("query", arguments)
            ids += [row["index"] for row in page["items"]]
            cursor = page["next_after"]
            assert page["truncated"] == (index < 2)
        assert ids == list(range(5)) and cursor is None
        for arguments in [
            {"collection": "private"},
            {"collection": "rows", "after_id": "not-an-id"},
            {"collection": "rows", "paginate": True, "projection": {"_id": 0}},
        ]:
            with pytest.raises((ValueError, PermissionError)):
                call("query", arguments)
        mutation = {
            "collection": "rows",
            "operation": "update_one",
            "filter": {"index": 0},
            "update": {"$set": {"reviewed": True}},
        }
        with pytest.raises(ValueError, match="confirm"):
            call("execute", mutation)
        assert not target.find_one({"index": 0}).get("reviewed")
        assert call("execute", mutation, confirm=True)["modified"] == 1
        assert target.find_one({"index": 0})["reviewed"]
    finally:
        studio.close()
        client.drop_database("nexus_acceptance")
        client.close()
