# Scoped SQL and enterprise connector acceptance

SQL uses pooled PostgreSQL/psycopg2, MySQL and MariaDB/PyMySQL,
SQL Server/pymssql, Oracle/python-oracledb connections. Omitted driver names select
those supported families. Other driver variants fail validation rather than lose
timeout guarantees. Read-only transactions remain the default; enabled writes
still require Nexus confirmation or the workflow approval gate. Mutation errors
never trigger automatic retries.

Connect limits are five seconds. PostgreSQL statement and lock limits are fifteen
seconds; MySQL and MariaDB set session statement limits. Their drivers also bound
network reads/writes. SQL Server uses login/query timeouts; Oracle uses connect
and checked-out connection call timeouts. Actual blocked-table SELECT checks on
PostgreSQL/MySQL/MariaDB now fail within 10–25 seconds and then successfully read
through the same pool after releasing the fixture lock. SQL Server/Oracle measured
timeouts, active cancellation and live cross-owner pool checks remain pending.

On 20 October 2026, isolated local Docker PostgreSQL 17, MySQL and MariaDB 11.4
passed actual driver schema reads, measured blocked-read timeout/pool recovery,
bound parameters, rejected writes before
approval, one approved commit, error recovery and pool reuse. Each used synthetic
data, a random loopback port, resource limits and no host data mounts. Owned
containers were removed. SQL Server and Oracle were not run; neither is labelled
live-verified. No configured remote Docker host was used.

The explicit optional suite is `tests/studio_sql_live_acceptance.py`. Set
`NEXUS_SQL_LIVE_FIXTURES` to a JSON object mapping selected dialect names to
**disposable test database** DSNs. It creates and removes `nexus_acceptance`.
Run only against isolated fixtures, never an existing user database. Missing
prerequisites fail explicitly rather than silently skipping. Connector credentials
remain server-side references, separate from exports and test reports.

MongoDB optional pagination uses `paginate: true`, stable ascending ObjectIds,
`limit` 1–100 and returned `next_after` as `after_id`. The selected collection and
original filter remain enforced. Projection cannot remove `_id`; non-ObjectId
pagination fails explicitly. Legacy query behavior is unchanged. A local MongoDB
8.2.12 container passed real filtered pagination and reviewed update acceptance;
its owned container was removed. `tests/studio_mongo_live_acceptance.py` is the
explicit optional suite.

Bedrock knowledge queries accept a bounded `next_token` and return one bounded
page plus the next token. Query, knowledge-base and result limit stay fixed across
pages. SDK fixtures verify these contracts. Actual Bedrock, Power BI and Nuclia
account checks remain pending.

No existing definitions need migration. Driver defaults are resolved on opening
new pooled connections; restart the server after installing optional drivers.
The existing Studio instance was not restarted during acceptance.
