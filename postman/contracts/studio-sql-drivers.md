# SQL driver acceptance

Approved driver families: PostgreSQL/psycopg2, MySQL and MariaDB/PyMySQL,
SQL Server/pymssql, Oracle/python-oracledb. Omitted driver names resolve to these
installed optional extras. Connections and queries have bounded driver timeouts;
PostgreSQL/MySQL/MariaDB also set statement limits. Read operations roll back;
only explicitly enabled, reviewed execute commits. No automatic mutation retries.
Live acceptance uses disposable local fixtures; unavailable dialect prerequisites
are reported separately, never silently skipped or labelled live-verified.
