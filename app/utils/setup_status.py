from pathlib import Path

import sqlalchemy
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.extensions import db

_MIGRATIONS_DIR = str(Path(__file__).resolve().parents[2] / "migrations")


def _head_revision():
    config = Config()
    config.set_main_option("script_location", _MIGRATIONS_DIR)
    return ScriptDirectory.from_config(config).get_current_head()


def _current_revision():
    with db.engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def is_setup_complete():
    """Schema at head AND at least one user exists — i.e. both `flask db
    upgrade` and the seed scripts have actually been run against this DB, not
    just the migration half. Anything short of that routes every request to
    the /setup wizard instead (see app/__init__.py's before_request hook).

    Deliberately not cached across requests: this app is small/low-traffic
    enough that two cheap queries per request are no real cost, and caching
    "complete" forever was actively dangerous — if someone drops tables on an
    already-set-up DB, a permanent cache would keep believing setup was still
    done, let requests through, and every DB query from then on would fail
    against a now-broken ORM session (see the rollback below) instead of
    cleanly bouncing back to /setup.
    """
    try:
        if _current_revision() != _head_revision():
            return False
        from app.models import User

        if User.query.first() is None:
            return False
        return True
    except Exception:
        # User.query.first() above uses the shared ORM session (db.session,
        # unlike the raw engine connections in _current_revision/
        # _head_revision) — if it fails (e.g. "relation users does not
        # exist"), Postgres leaves that session's transaction aborted until
        # explicitly rolled back. Without this, every later query in the
        # same request (current_user.is_authenticated, error logging, ...)
        # would fail too, with a confusing "current transaction is aborted"
        # instead of the real error.
        db.session.rollback()
        return False


def db_connection_status():
    """(ok, error_message) from a trivial query against the configured DATABASE_URL."""
    try:
        with db.engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, str(exc)


def db_is_empty():
    """Whether the connected database has no tables at all yet, vs. already
    having some schema (a prior partial setup, or an existing database this
    app is being pointed at) — purely informational on the setup page, never
    blocks clicking the setup button either way.
    """
    try:
        return len(sqlalchemy.inspect(db.engine).get_table_names()) == 0
    except Exception:
        return True
