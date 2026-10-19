"""Identity-scoped SQL pools and bounded, explicitly reviewed SQL operations."""

import hashlib
import json
import os
import re

DIALECTS = {"postgresql", "mysql", "mariadb", "mssql", "oracle"}


def validate_read_query(query):
    # SELECT INTO can create objects or export files, even though SELECT is its
    # first token. Do not treat it or server/remote execution as a read action.
    # Database accounts must still have read-only privileges; custom database
    # routines are governed by those privileges, not by this lexical guard.
    if not isinstance(query, str) or not 1 <= len(query) <= 16000 or "\x00" in query:
        raise ValueError("SQL query must be text up to 16000 characters")
    if (
        not re.match(r"\A\s*SELECT\b", query, re.I)
        or any(value in query for value in (";", "--", "/*", "*/", "\\"))
        or re.search(
            r"\b(?:INTO|OPENROWSET|OPENDATASOURCE|OPENQUERY|EXEC(?:UTE)?|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|CALL|COPY|LOAD_FILE|SLEEP|PG_SLEEP|GET_LOCK|DBMS_LOCK|XP_\w+|SP_\w+)\b",
            query,
            re.I,
        )
    ):
        raise ValueError(
            "Read queries require one SELECT without object creation, exports, locks or server execution; use parameters for literal values"
        )
    return query


def validate_sql(data):
    from nexus.core.studio_connections import env_name

    dialect = data.get("dialect")
    if dialect not in DIALECTS:
        raise ValueError("Choose PostgreSQL, MySQL, MariaDB, SQL Server or Oracle")
    writes = data.get("allow_writes", False)
    if type(writes) is not bool or type(data.get("enabled", False)) is not bool:
        raise ValueError("Enabled and allow writes must be true or false")
    actions = data.get("actions", ["query", "inspect_schema"])
    if not isinstance(actions, list) or set(actions) - {"query", "inspect_schema", "execute"}:
        raise ValueError("Choose query, inspect_schema or execute SQL actions")
    if "execute" in actions and not writes:
        raise ValueError("Enable writes explicitly before adding execute action")
    return {
        "type": "sql",
        "dialect": dialect,
        "dsn_env": env_name(data.get("dsn_env")),
        "enabled": data.get("enabled", False),
        "allow_writes": writes,
        "actions": actions,
    }


class StudioSQLConnections:
    def __init__(self, studio):
        self.studio, self.pools = studio, {}

    def close(self):
        for pool in self.pools.values():
            pool.dispose()
        self.pools.clear()

    def call(self, data, identity, action, arguments):
        if action == "query":
            validate_read_query(arguments.get("query"))
        try:
            import sqlalchemy as sa
        except ImportError as exc:
            raise ValueError(
                "Install the SQL connector dependencies and selected database driver"
            ) from exc
        values = os.environ
        if self.studio.team:
            self.studio.team.assert_execution(identity)
            values, error = self.studio.team.environment_resolver(identity)
            if error:
                raise PermissionError("SQL account credentials unavailable")
        dsn = values.get(data["dsn_env"])
        if not dsn:
            raise PermissionError("Configure SQL connection environment reference")
        url = sa.engine.make_url(dsn)
        if url.get_backend_name() != data["dialect"]:
            raise ValueError("Connection dialect does not match configured database")
        key = hashlib.sha256(json.dumps([identity, data, dsn], sort_keys=True).encode()).hexdigest()
        with self.studio.lock:
            if key not in self.pools:
                if len(self.pools) >= 32:
                    _, old = self.pools.popitem()
                    old.dispose()
                self.pools[key] = sa.create_engine(
                    url,
                    pool_pre_ping=True,
                    pool_size=2,
                    max_overflow=2,
                    pool_timeout=10,
                    pool_recycle=300,
                )
            engine = self.pools[key]
        try:
            with engine.connect() as connection:
                if action == "inspect_schema":
                    inspector = sa.inspect(connection)
                    names = inspector.get_table_names()[:100]
                    return {
                        "tables": [
                            {
                                "name": name,
                                "columns": [
                                    {"name": c["name"], "type": str(c["type"])}
                                    for c in inspector.get_columns(name)[:100]
                                ],
                            }
                            for name in names
                        ]
                    }
                query = arguments.get("query")
                if not isinstance(query, str) or not 1 <= len(query) <= 16000 or "\x00" in query:
                    raise ValueError("SQL query must be text up to 16000 characters")
                parameters = arguments.get("parameters", {})
                if not isinstance(parameters, dict):
                    raise ValueError("SQL parameters must be an object")
                if action == "query":
                    if data["dialect"] == "postgresql":
                        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                        connection.exec_driver_sql("SET LOCAL statement_timeout = 15000")
                    elif data["dialect"] in {"mysql", "mariadb"}:
                        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    elif data["dialect"] == "oracle":
                        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                elif action != "execute" or not data["allow_writes"]:
                    raise PermissionError("SQL writes are disabled")
                result = connection.execute(sa.text(query), parameters)
                rows = result.fetchmany(101) if result.returns_rows else []
                record = {
                    "columns": list(result.keys()) if result.returns_rows else [],
                    "rows": [list(row) for row in rows[:100]],
                    "truncated": len(rows) > 100,
                    "rowcount": result.rowcount,
                }
                if len(json.dumps(record, default=str).encode()) > 262144:
                    raise ValueError("SQL response exceeds 256 KiB")
                if action == "execute":
                    connection.commit()
                else:
                    connection.rollback()
                return record
        except (ValueError, PermissionError):
            raise
        except Exception as exc:
            raise ValueError(
                "SQL operation failed; verify database availability, driver and account permissions"
            ) from exc
