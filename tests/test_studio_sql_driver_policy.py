"""Every supported dialect selects a driver with bounded connection/query I/O."""

import pytest
import sqlalchemy as sa

from nexus.core.studio_sql_connectors import DRIVERS, driver_options
from tests.test_studio_team import team as team


@pytest.mark.parametrize("dialect", sorted(DRIVERS))
def test_default_driver_has_explicit_bounded_connect_policy(dialect):
    url, options = driver_options(
        sa.engine.make_url(dialect + "://user:password@localhost/example")
    )
    assert url.drivername == dialect + "+" + DRIVERS[dialect]
    assert options and all(value for value in options.values())
    assert (
        "connect_timeout" in options
        or "login_timeout" in options
        or "tcp_connect_timeout" in options
    )
    assert driver_options(url) == (url, options)


@pytest.mark.parametrize("dialect", sorted(DRIVERS))
def test_unreviewed_driver_fails_before_pool_creation(dialect):
    with pytest.raises(ValueError, match="supported SQL driver"):
        driver_options(sa.engine.make_url(dialect + "+unbounded://localhost/example"))


@pytest.mark.parametrize("dialect", sorted(DRIVERS))
def test_each_dialect_reviews_writes_scopes_pools_and_recovers_errors(team, monkeypatch, dialect):
    """Library-boundary fixtures supplement, never replace, real server acceptance."""
    import asyncio
    from types import SimpleNamespace

    studio, policy, _, _, _ = team
    engines, queries = [], []
    credentials = {"alice": "alice-v1", "bob": "bob-v1"}
    policy.environment_resolver = lambda owner: (
        {"SQL_MATRIX_DSN": f"{dialect}://{owner}:{credentials[owner]}@fixture.invalid/evidence"},
        None,
    )

    class Connection:
        def __init__(self, engine):
            self.engine = engine
            self.pending = None
            self.connection = SimpleNamespace(driver_connection=SimpleNamespace(call_timeout=None))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.pending = None
            self.engine.closed += 1

        def exec_driver_sql(self, text):
            queries.append((self.engine.owner, text, {}))

        def commit(self):
            if self.pending is not None:
                self.engine.value = self.pending
                self.pending = None

        def rollback(self):
            self.pending = None

        def execute(self, text, parameters):
            query = str(text)
            queries.append((self.engine.owner, query, parameters))
            if "missing_column" in query:
                raise RuntimeError("DO_NOT_LEAK_DRIVER_PASSWORD")
            writing = query.startswith("UPDATE")
            if writing:
                self.pending = parameters["value"]
            return SimpleNamespace(
                returns_rows=not writing,
                rowcount=1,
                keys=lambda: ["value"],
                fetchmany=lambda _: [[self.engine.value]],
            )

    class Engine:
        def __init__(self, owner):
            self.owner, self.value, self.closed, self.disposed = owner, "before", 0, False

        def connect(self):
            return Connection(self)

        def dispose(self):
            self.disposed = True

    def create(url, **kwargs):
        engine = Engine(url.username)
        engines.append(engine)
        return engine

    monkeypatch.setattr(sa, "create_engine", create)
    monkeypatch.setattr(
        sa,
        "inspect",
        lambda connection: SimpleNamespace(
            get_table_names=lambda: ["evidence"],
            get_columns=lambda name: [{"name": "value", "type": "text"}],
        ),
    )
    record = studio.catalog.save(
        "connectors",
        {
            "name": "SQL matrix",
            "requester_nexus_id": "alice",
            "data": {
                "type": "sql",
                "dialect": dialect,
                "dsn_env": "SQL_MATRIX_DSN",
                "enabled": True,
                "allow_writes": True,
                "actions": ["query", "inspect_schema", "execute"],
            },
        },
    )

    def call(
        owner="alice",
        action="query",
        confirm=False,
        query="SELECT value FROM evidence WHERE id=:id",
    ):
        return asyncio.run(
            studio.connections.execute(
                record["id"],
                {
                    "action": action,
                    "confirm": confirm,
                    "arguments": {"query": query, "parameters": {"id": 1, "value": "after"}},
                },
                owner,
            )
        )

    try:
        assert call(action="inspect_schema")["tables"][0]["name"] == "evidence"
        assert call()["rows"] == [["before"]]
        count = len(queries)
        with pytest.raises(ValueError, match="confirm"):
            call(action="execute", query="UPDATE evidence SET value=:value WHERE id=:id")
        assert len(queries) == count
        call(action="execute", confirm=True, query="UPDATE evidence SET value=:value WHERE id=:id")
        assert call()["rows"] == [["after"]]
        assert call(owner="bob")["rows"] == [["before"]]
        assert len(engines) == 2
        with pytest.raises(ValueError, match="Integration action failed") as error:
            call(query="SELECT missing_column FROM evidence")
        assert "DO_NOT_LEAK" not in str(error.value)
        assert call()["rows"] == [["after"]] and len(engines) == 2
        credentials["alice"] = "alice-v2"
        assert call()["rows"] == [["before"]] and len(engines) == 3
        policy.bindings["bob"] = "viewer"
        count = len(queries)
        with pytest.raises(PermissionError):
            call(owner="bob")
        assert len(queries) == count
    finally:
        studio.connections.sql.close()
    assert all(engine.disposed and engine.closed > 0 for engine in engines)
