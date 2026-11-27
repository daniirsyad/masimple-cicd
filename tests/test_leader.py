import sqlalchemy

from app.extensions import db
from app.utils import leader

TEST_KEY = 917_442_199


def test_become_leader_holds_the_lock_for_other_processes(app):
    leader.become_leader(app, TEST_KEY)
    try:
        with app.app_context(), db.engine.connect() as other:
            # Another session (another gunicorn process) can't take it.
            assert other.execute(sqlalchemy.text("SELECT pg_try_advisory_lock(:k)"), {"k": TEST_KEY}).scalar() is False
    finally:
        leader._held.pop(TEST_KEY).close()
