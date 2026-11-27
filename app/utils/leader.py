from app.extensions import db

# key -> the connection holding that lock, kept open for the process's life.
_held = {}


def become_leader(app, key):
    """Blocks until this process holds Postgres advisory lock `key`, making
    it the only gunicorn worker process that runs a given background job.

    gunicorn runs several worker processes (entrypoint.sh), each starting its
    own background threads. That's fine for the build/deploy/workflow queues
    (each claim is `SELECT ... FOR UPDATE SKIP LOCKED`), but not for a job
    that must run once app-wide: Telegram's getUpdates (a second poller gets
    409), Discord's Gateway connection, or the live-status poller (else 3x
    the kubectl/helm calls).

    The lock is taken on a dedicated connection, detached from the
    SQLAlchemy pool so it's never recycled or counted against pool_size, and
    kept open for the process's life. `pg_advisory_lock` is session-scoped,
    so it survives the commit; if this process dies, Postgres releases it
    and the next process waiting here takes over.
    """
    with app.app_context():
        raw_conn = db.engine.raw_connection()
        raw_conn.detach()
        cursor = raw_conn.cursor()
        cursor.execute("SELECT pg_advisory_lock(%s)", (key,))
        cursor.close()
        raw_conn.commit()
    _held[key] = raw_conn
