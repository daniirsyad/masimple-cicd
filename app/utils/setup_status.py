import functools
from pathlib import Path

import sqlalchemy
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.extensions import db

_MIGRATIONS_DIR = str(Path(__file__).resolve().parents[2] / "migrations")


@functools.cache
def _head_revision():
    """The newest migration in migrations/versions. Parsing those files takes
    a few ms, and they only change with a new deploy (a restart), so it's
    read once per process."""
    config = Config()
    config.set_main_option("script_location", _MIGRATIONS_DIR)
    return ScriptDirectory.from_config(config).get_current_head()


def is_setup_complete():
    """Schema at head AND at least one user exists — i.e. both `flask db
    upgrade` and the seed scripts have actually been run against this DB, not
    just the migration half. Anything short of that routes every request to
    the /setup wizard instead (see app/__init__.py's before_request hook).

    The database side is deliberately not cached: if someone drops tables on
    an already-set-up DB, a cached "complete" would keep letting requests
    through to fail against the broken schema instead of bouncing to /setup.
    It's one query (app/__init__.py runs it once per request), on its own
    engine connection, so a missing table can't leave db.session's
    transaction aborted for the rest of the request.
    """
    try:
        with db.engine.connect() as conn:
            current, has_user = conn.execute(
                sqlalchemy.text(
                    "SELECT (SELECT version_num FROM alembic_version LIMIT 1), EXISTS (SELECT 1 FROM users)"
                )
            ).one()
    except Exception:
        return False
    return bool(has_user) and current == _head_revision()


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
