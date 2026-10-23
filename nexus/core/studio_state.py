"""Optional transactional SQL state and a leased, non-replaying Studio job queue.

PostgreSQL supports separate HTTP/worker processes. SQLite exercises the same
contract locally. SQL mode requires a shared definitions/bytes filesystem unless
artifacts use an external store. Credentials are resolved only when jobs execute.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from contextlib import contextmanager

job_fence = contextvars.ContextVar("studio_sql_job_fence", default=None)


class LostLeaseError(RuntimeError):
    pass


class SQLRecordPath:
    def __init__(self, store, collection, identity, path):
        self.store, self.collection, self.identity, self.path = store, collection, identity, path

    def exists(self):
        return self.store.get(self.collection, self.identity, missing=True) is not None

    def read_bytes(self):
        return json.dumps(
            self.store.get(self.collection, self.identity), ensure_ascii=False, sort_keys=True
        ).encode()

    def unlink(self, missing_ok=False):
        self.store.delete(self.collection, self.identity, missing_ok)

    def __getattr__(self, name):
        return getattr(self.path, name)


class SQLWorkspaceLock:
    """Reentrant in-process lock plus one transaction-scoped project row lock."""

    def __init__(self, store):
        self.store = store
        self.mutex = threading.RLock()
        self.local = threading.local()

    def __enter__(self):
        self.mutex.acquire()
        if getattr(self.local, "depth", 0):
            self.local.depth += 1
            return self
        try:
            conn = self.store.engine.connect()
            try:
                if conn.dialect.name == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    conn.begin()
                conn.execute(
                    self.store.sa.select(self.store.locks.c.namespace)
                    .where(self.store.locks.c.namespace == self.store.namespace)
                    .with_for_update()
                ).first()
            except BaseException:
                conn.close()
                raise
            self.store.local.connection = conn
            self.local.depth = 1
            self.local.failed = False
            self.local.after_commit = []
            self.local.after_rollback = []
            return self
        except BaseException:
            self.mutex.release()
            raise

    def __exit__(self, kind, value, tb):
        self.local.failed = self.local.failed or kind is not None
        self.local.depth -= 1
        try:
            if self.local.depth == 0:
                conn = self.store.local.connection
                committed = False
                try:
                    if self.local.failed:
                        conn.rollback()
                    else:
                        conn.commit()
                        committed = True
                finally:
                    self.store.local.connection = None
                    conn.close()
                    callbacks = self.local.after_commit if committed else self.local.after_rollback
                    self.local.after_commit, self.local.after_rollback = [], []
                    for callback in callbacks:
                        callback()
        finally:
            self.mutex.release()

    def on_transaction_end(self, *, committed, rolled_back):
        """Finalize accompanying file journals only after the outer SQL transaction ends."""
        if not getattr(self.local, "depth", 0):
            raise RuntimeError("Transaction callbacks require an active workspace lock")
        self.local.after_commit.append(committed)
        self.local.after_rollback.append(rolled_back)


def _psycopg2_url(url):
    """Pin bare PostgreSQL URLs to the psycopg2 driver shipped by the postgres extra.

    SQLAlchemy 2.1 maps ``postgresql://`` to psycopg 3, which that extra does not install.
    """
    text = str(url or "")
    for prefix in ("postgresql://", "postgres://"):
        if text.startswith(prefix):
            return "postgresql+psycopg2://" + text[len(prefix) :]
    return url


class StudioSQLState:
    def __init__(self, url, namespace):
        try:
            import sqlalchemy as sa
        except ImportError:
            raise ValueError("Install nexus-arc[postgres] for SQL Studio state") from None
        if not isinstance(namespace, str) or not namespace or len(namespace) > 96:
            raise ValueError(
                "SQL state requires an explicit workspace namespace (up to 96 characters)"
            )
        self.sa, self.namespace, self.local = sa, namespace, threading.local()
        self.engine = sa.create_engine(
            _psycopg2_url(url), pool_pre_ping=True, hide_parameters=True
        )
        if self.engine.dialect.name not in {"postgresql", "sqlite"}:
            self.engine.dispose()
            raise ValueError("Studio SQL state supports PostgreSQL or SQLite")
        schema = sa.MetaData()
        self.table = sa.Table(
            "nexus_studio_records",
            schema,
            sa.Column("namespace", sa.String(96), primary_key=True),
            sa.Column("collection", sa.String(512), primary_key=True),
            sa.Column("identity", sa.String(96), primary_key=True),
            sa.Column("body", sa.JSON, nullable=False),
        )
        self.locks = sa.Table(
            "nexus_studio_locks", schema, sa.Column("namespace", sa.String(96), primary_key=True)
        )
        self.jobs = sa.Table(
            "nexus_studio_jobs",
            schema,
            sa.Column("namespace", sa.String(96), primary_key=True),
            sa.Column("run_id", sa.String(96), primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
            sa.Column("session_id", sa.String(96), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("token", sa.String(32)),
            sa.Column("expires", sa.Float),
            sa.Column("created", sa.Float, nullable=False),
        )
        schema.create_all(self.engine)
        from sqlalchemy.exc import IntegrityError

        try:
            with self.engine.begin() as conn:
                conn.execute(self.locks.insert().values(namespace=namespace))
        except IntegrityError:
            pass
        self.lock = SQLWorkspaceLock(self)

    @contextmanager
    def transaction(self):
        active = getattr(self.local, "connection", None)
        if active is not None:
            yield active
        else:
            with self.engine.begin() as conn:
                yield conn

    def where(self, collection, identity):
        return self.sa.and_(
            self.table.c.namespace == self.namespace,
            self.table.c.collection == collection,
            self.table.c.identity == identity,
        )

    def check_fence(self, conn):
        fence = job_fence.get()
        if fence:
            run_id, token = fence
            row = conn.execute(
                self.sa.select(self.jobs.c.token, self.jobs.c.expires, self.jobs.c.status)
                .where(self.jobs.c.namespace == self.namespace, self.jobs.c.run_id == run_id)
                .with_for_update()
            ).first()
            if (
                not row
                or row.token != token
                or row.status != "running"
                or row.expires <= time.time()
            ):
                raise LostLeaseError("Worker lease lost; execution results are fenced")

    def get(self, collection, identity, missing=False):
        with self.transaction() as conn:
            row = conn.execute(
                self.sa.select(self.table.c.body).where(self.where(collection, identity))
            ).first()
        if row is None and not missing:
            raise FileNotFoundError("Studio record not found")
        return row[0] if row else None

    def put(self, collection, identity, body):
        with self.transaction() as conn:
            self.check_fence(conn)
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = (pg_insert if conn.dialect.name == "postgresql" else sqlite_insert)(
                self.table
            ).values(namespace=self.namespace, collection=collection, identity=identity, body=body)
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["namespace", "collection", "identity"], set_={"body": body}
                )
            )

    def delete(self, collection, identity, missing_ok=False):
        with self.transaction() as conn:
            self.check_fence(conn)
            count = conn.execute(
                self.table.delete().where(self.where(collection, identity))
            ).rowcount
        if not count and not missing_ok:
            raise FileNotFoundError("Studio record not found")

    def records(self, collection):
        with self.transaction() as conn:
            return list(
                conn.execute(
                    self.sa.select(self.table.c.body).where(
                        self.table.c.namespace == self.namespace,
                        self.table.c.collection == collection,
                    )
                ).scalars()
            )

    def enqueue(self, run_id, payload, session_id):
        # Safe JSON copy rejects callbacks, credentials embedded as objects, etc.
        payload = json.loads(json.dumps(payload))
        with self.transaction() as conn:
            existing = conn.execute(
                self.sa.select(self.jobs).where(
                    self.jobs.c.namespace == self.namespace, self.jobs.c.run_id == run_id
                )
            ).first()
            if existing and existing.status in {"queued", "running"}:
                raise ValueError("This run is already queued or executing")
            self.delete("job-resumptions", run_id, missing_ok=True)
            if existing:
                conn.execute(
                    self.jobs.delete().where(
                        self.jobs.c.namespace == self.namespace, self.jobs.c.run_id == run_id
                    )
                )
            conn.execute(
                self.jobs.insert().values(
                    namespace=self.namespace,
                    run_id=run_id,
                    payload=payload,
                    session_id=session_id,
                    status="queued",
                    created=time.time(),
                )
            )

    def resume(self, run_id, payload, session_id):
        """Fence a requested continuation behind the currently finishing worker."""
        payload = json.loads(json.dumps(payload))
        with self.lock:
            with self.transaction() as conn:
                row = (
                    conn.execute(
                        self.sa.select(self.jobs).where(
                            self.jobs.c.namespace == self.namespace, self.jobs.c.run_id == run_id
                        )
                    )
                    .mappings()
                    .first()
                )
                if row and row["status"] == "running":
                    run = self.get("runs", run_id)
                    if run["status"] != "queued" or not (
                        payload.get("resume_graph") or payload.get("resume_yaml")
                    ):
                        raise ValueError(
                            "Only an approved workflow continuation can wait for its worker"
                        )
                    receipt = {
                        "format": "nexus-workflow-resumption/v1",
                        "run_id": run_id,
                        "owner_id": run.get("requester_nexus_id"),
                        "source_token": row["token"],
                        "payload": payload,
                        "session_id": session_id,
                    }
                    pending = self.get("job-resumptions", run_id, missing=True)
                    if pending and pending != receipt:
                        raise ValueError("This workflow already has a pending continuation")
                    self.put("job-resumptions", run_id, receipt)
                    return
                self.enqueue(run_id, payload, session_id)

    def claim(self, lease_seconds):
        with self.lock:
            with self.transaction() as conn:
                row = (
                    conn.execute(
                        self.sa.select(self.jobs)
                        .where(
                            self.jobs.c.namespace == self.namespace, self.jobs.c.status == "queued"
                        )
                        .order_by(self.jobs.c.created)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                    .mappings()
                    .first()
                )
                if not row:
                    return None
                token = uuid.uuid4().hex
                conn.execute(
                    self.jobs.update()
                    .where(
                        self.jobs.c.namespace == self.namespace, self.jobs.c.run_id == row["run_id"]
                    )
                    .values(status="running", token=token, expires=time.time() + lease_seconds)
                )
                return {**row, "token": token}

    def heartbeat(self, run_id, token, lease_seconds):
        with self.transaction() as conn:
            count = conn.execute(
                self.jobs.update()
                .where(
                    self.jobs.c.namespace == self.namespace,
                    self.jobs.c.run_id == run_id,
                    self.jobs.c.token == token,
                    self.jobs.c.status == "running",
                    self.jobs.c.expires > time.time(),
                )
                .values(expires=time.time() + lease_seconds)
            ).rowcount
            return count == 1

    def finish(self, run_id, token):
        with self.lock, self.transaction() as conn:
            pending = self.get("job-resumptions", run_id, missing=True)
            row = conn.execute(
                self.sa.select(self.jobs).where(
                    self.jobs.c.namespace == self.namespace,
                    self.jobs.c.run_id == run_id,
                    self.jobs.c.token == token,
                    self.jobs.c.status == "running",
                )
            ).first()
            if row and pending and pending.get("source_token") == token:
                run = self.get("runs", run_id)
                if run["status"] in {"queued", "waiting"}:
                    conn.execute(
                        self.jobs.update()
                        .where(
                            self.jobs.c.namespace == self.namespace,
                            self.jobs.c.run_id == run_id,
                            self.jobs.c.token == token,
                            self.jobs.c.status == "running",
                        )
                        .values(
                            payload=pending["payload"],
                            session_id=pending["session_id"],
                            status="queued",
                            token=None,
                            expires=None,
                            created=time.time(),
                        )
                    )
                    run["status"] = "queued"
                    if run.get("cancellation_cleanup"):
                        run["cleanup_status"] = "queued"
                    self.put("runs", run_id, run)
                    self.delete("job-resumptions", run_id)
                    return
                self.delete("job-resumptions", run_id)
            conn.execute(
                self.jobs.update()
                .where(
                    self.jobs.c.namespace == self.namespace,
                    self.jobs.c.run_id == run_id,
                    self.jobs.c.token == token,
                    self.jobs.c.status == "running",
                )
                .values(status="finished", expires=None)
            )

    def reap(self, on_interrupted=None):
        """Interrupt runs whose worker lease expired; return how many were reaped."""
        with self.lock:
            with self.transaction() as conn:
                rows = (
                    conn.execute(
                        self.sa.select(self.jobs)
                        .where(
                            self.jobs.c.namespace == self.namespace,
                            self.jobs.c.status == "running",
                            self.jobs.c.expires <= time.time(),
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .all()
                )
                for row in rows:
                    conn.execute(
                        self.jobs.update()
                        .where(
                            self.jobs.c.namespace == self.namespace,
                            self.jobs.c.run_id == row["run_id"],
                        )
                        .values(status="interrupted", token=None)
                    )
                    run = self.get("runs", row["run_id"], missing=True)
                    if run and run["status"] in {"running", "queued"}:
                        run.update(
                            status="interrupted",
                            error="Worker lease expired. Review external effects before resuming.",
                        )
                        self.put("runs", row["run_id"], run)
                    remote = self.get("external-runs", row["run_id"], missing=True)
                    if remote and remote.get("status") not in {
                        "completed",
                        "failed",
                        "canceled",
                        "cancelled",
                        "rejected",
                        "input-required",
                        "auth-required",
                    }:
                        remote["status"] = "uncertain"
                        self.put("external-runs", row["run_id"], remote)
                    campaign_id = row["payload"].get("campaign_id")
                    campaign = (
                        self.get("campaigns", campaign_id, missing=True) if campaign_id else None
                    )
                    if campaign and campaign.get("active_run") == row["run_id"]:
                        campaign.update(
                            status="uncertain",
                            recovery_note="Worker lease expired during this attempt. Review its remote outcomes before another publish.",
                        )
                        campaign.pop("active_run", None)
                        self.put("campaigns", campaign_id, campaign)
        if on_interrupted:
            for row in rows:
                try:
                    on_interrupted(row["run_id"], row["payload"])
                except Exception:  # the lease is already settled; keep reaping
                    pass
        return len(rows)


class StudioDurableExecutor:
    def __init__(self, studio, workers=4, lease_seconds=60):
        if type(workers) is not int or not 0 <= workers <= 16:
            raise ValueError("Choose zero to sixteen Studio workers")
        self.studio, self.store = studio, studio.sql_state
        self.lease_seconds, self.stop = lease_seconds, threading.Event()
        self.threads = [
            threading.Thread(target=self.worker, name=f"nexus-durable-{i}", daemon=True)
            for i in range(workers)
        ]
        for thread in self.threads:
            thread.start()

    def submit(self, callback, run_id, payload, session_id):
        if callback != self.studio._execute:
            raise ValueError("Durable executor accepts Studio execution jobs only")
        self.store.enqueue(run_id, payload, session_id)

    def resume(self, run_id, payload, session_id):
        self.store.resume(run_id, payload, session_id)

    def worker(self):
        while not self.stop.is_set():
            try:
                if self.store.reap(self.studio.save_unsuccessful_turn):
                    self.studio.speech.cleanup()
                job = self.store.claim(self.lease_seconds)
                if not job:
                    self.stop.wait(0.2)
                    continue
                done = threading.Event()

                def renew(job=job, done=done):
                    while not done.wait(self.lease_seconds / 3):
                        try:
                            valid = self.store.heartbeat(
                                job["run_id"], job["token"], self.lease_seconds
                            )
                        except Exception:
                            valid = False
                        try:
                            requested = self.studio.run(job["run_id"]).get("cancel_requested")
                        except Exception:
                            requested = False
                        if requested and not self.studio.run(job["run_id"]).get(
                            "cancellation_cleanup"
                        ):
                            with self.studio.lock:
                                active = self.studio.active_tasks.get(job["run_id"])
                            if active:

                                def cancel_requested_task(active=active):
                                    if not self.studio.run(job["run_id"]).get(
                                        "cancellation_cleanup"
                                    ):
                                        active[1].cancel()

                                active[0].call_soon_threadsafe(cancel_requested_task)
                        if not valid:
                            with self.studio.lock:
                                active = self.studio.active_tasks.get(job["run_id"])
                            if active:
                                active[0].call_soon_threadsafe(active[1].cancel)
                            return

                heartbeat = threading.Thread(target=renew, daemon=True, name="nexus-job-lease")
                heartbeat.start()
                fence = job_fence.set((job["run_id"], job["token"]))
                try:
                    self.studio._execute(job["run_id"], job["payload"], job["session_id"])
                finally:
                    job_fence.reset(fence)
                    done.set()
                    heartbeat.join(timeout=2)
                    self.store.finish(job["run_id"], job["token"])
                    if job["payload"].get("kind") == "speech":
                        self.studio.speech.cleanup()
            except Exception:
                # Do not replay ambiguous attempts. A lease expiration exposes
                # interruption; an operator reviews persisted checkpoints/effects.
                self.stop.wait(0.2)

    def shutdown(self, wait=True, cancel_futures=False):
        self.stop.set()
        if wait:
            for thread in self.threads:
                thread.join()


class StudioWorkflowStorage:
    """The workflow engine's persisted workflow/audit operations over Studio state."""

    def __init__(self, studio):
        self.studio = studio

    async def save_workflow(self, workflow):
        from nexus.adapters.storage._workflow_serde import workflow_to_dict

        self.studio._write(
            self.studio._path("yaml-runtime", workflow.id), workflow_to_dict(workflow)
        )

    async def load_workflow(self, workflow_id):
        from nexus.adapters.storage._workflow_serde import dict_to_workflow

        path = self.studio._path("yaml-runtime", workflow_id)
        return dict_to_workflow(self.studio._read(path)) if path.exists() else None

    async def append_audit_event(self, event):
        from dataclasses import asdict

        data = json.loads(json.dumps(asdict(event), default=str))
        self.studio._write(self.studio._path("yaml-audit", uuid.uuid4().hex), data)
