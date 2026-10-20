"""Explicit optional live driver suite; requires an isolated service DSN per dialect."""

import asyncio
import json
import os

import pytest
import sqlalchemy as sa

from nexus.core.studio import StudioService

DSNS = json.loads(os.environ.get("NEXUS_SQL_LIVE_FIXTURES", "{}"))


@pytest.mark.parametrize("dialect", list(DSNS) or ["fixture-prerequisite-missing"])
def test_real_driver_reads_schema_binds_and_reviewed_commit(tmp_path, monkeypatch, dialect):
    assert dialect in DSNS, (
        "Set NEXUS_SQL_LIVE_FIXTURES to disposable local database DSNs; no skips"
    )
    dsn = DSNS[dialect]
    engine = sa.create_engine(dsn)
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE nexus_acceptance (id INTEGER PRIMARY KEY, value VARCHAR(80))")
        )
        connection.execute(sa.text("INSERT INTO nexus_acceptance (id, value) VALUES (1, 'before')"))
    monkeypatch.setenv("LIVE_ACCEPTANCE_DSN", dsn)
    studio = StudioService(tmp_path)
    try:
        connector = studio.catalog.save(
            "connectors",
            {
                "name": "Live " + dialect,
                "data": {
                    "type": "sql",
                    "dialect": dialect,
                    "dsn_env": "LIVE_ACCEPTANCE_DSN",
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

        schema = call("inspect_schema", {})
        assert any(table["name"].lower() == "nexus_acceptance" for table in schema["tables"])
        assert call(
            "query",
            {"query": "SELECT value FROM nexus_acceptance WHERE id=:id", "parameters": {"id": 1}},
        )["rows"] == [["before"]]
        mutation = {
            "query": "UPDATE nexus_acceptance SET value=:value WHERE id=:id",
            "parameters": {"value": "after", "id": 1},
        }
        with pytest.raises(ValueError, match="confirm"):
            call("execute", mutation)
        assert call("query", {"query": "SELECT value FROM nexus_acceptance WHERE id=1"})[
            "rows"
        ] == [["before"]]
        call("execute", mutation, confirm=True)
        assert call("query", {"query": "SELECT value FROM nexus_acceptance WHERE id=1"})[
            "rows"
        ] == [["after"]]
        assert len(studio.connections.sql.pools) == 1
        for invalid in [
            "DELETE FROM nexus_acceptance",
            "SELECT * INTO another_table FROM nexus_acceptance",
            "SELECT 1; DELETE FROM nexus_acceptance",
        ]:
            with pytest.raises(ValueError):
                call("query", {"query": invalid})
        with pytest.raises(ValueError):
            call("query", {"query": "SELECT missing_column FROM nexus_acceptance"})
        assert call("query", {"query": "SELECT value FROM nexus_acceptance WHERE id=1"})[
            "rows"
        ] == [["after"]]
    finally:
        studio.close()
        with engine.begin() as connection:
            connection.execute(sa.text("DROP TABLE nexus_acceptance"))
        engine.dispose()


@pytest.mark.parametrize(
    "dialect",
    [name for name in DSNS if name in {"postgresql", "mysql", "mariadb"}]
    or ["timeout-prerequisite-missing"],
)
def test_real_blocked_read_times_out_and_pool_recovers(tmp_path, monkeypatch, dialect):
    import concurrent.futures
    import time

    assert dialect in DSNS, "Timeout acceptance needs an isolated PostgreSQL/MySQL/MariaDB fixture"
    engine = sa.create_engine(DSNS[dialect])
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE nexus_timeout_acceptance (id INTEGER PRIMARY KEY)")
        )
        connection.execute(sa.text("INSERT INTO nexus_timeout_acceptance (id) VALUES (1)"))
    locker = engine.connect()
    locker.exec_driver_sql(
        "LOCK TABLE nexus_timeout_acceptance IN ACCESS EXCLUSIVE MODE"
        if dialect == "postgresql"
        else "LOCK TABLES nexus_timeout_acceptance WRITE"
    )
    monkeypatch.setenv("TIMEOUT_ACCEPTANCE_DSN", DSNS[dialect])
    studio = StudioService(tmp_path)
    try:
        connector = studio.catalog.save(
            "connectors",
            {
                "name": "Blocked reader",
                "data": {
                    "type": "sql",
                    "dialect": dialect,
                    "dsn_env": "TIMEOUT_ACCEPTANCE_DSN",
                    "enabled": True,
                },
            },
        )

        def query(text):
            return asyncio.run(
                studio.connections.execute(
                    connector["id"], {"action": "query", "arguments": {"query": text}}
                )
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as workers:
            start = time.monotonic()
            future = workers.submit(query, "SELECT id FROM nexus_timeout_acceptance")
            time.sleep(0.25)
            assert not future.done(), "Fixture did not actually block the selected read"
            try:
                with pytest.raises(ValueError):
                    future.result(timeout=25)
            finally:
                # Release before joining the worker, even if timeout enforcement fails.
                if dialect != "postgresql":
                    locker.exec_driver_sql("UNLOCK TABLES")
                locker.rollback()
            elapsed = time.monotonic() - start
            assert 10 <= elapsed < 25, f"Unexpected driver timeout duration: {elapsed}"
        assert query("SELECT id FROM nexus_timeout_acceptance")["rows"] == [[1]]
    finally:
        locker.close()
        studio.close()
        with engine.begin() as connection:
            connection.execute(sa.text("DROP TABLE nexus_timeout_acceptance"))
        engine.dispose()
