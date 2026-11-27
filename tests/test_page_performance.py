"""Per-page overhead: static caching, Server-Timing, the shared per-page queries."""

import re

from flask import url_for

from app.extensions import db
from app.models import User
from app.utils.setup_status import is_setup_complete
from app.utils.system_config import get_system_config


def _query_count(response):
    return int(re.search(r'db;desc="(\d+) queries"', response.headers["Server-Timing"]).group(1))


def test_static_urls_are_versioned_and_cached(app, client):
    with app.test_request_context():
        url = url_for("static", filename="js/page.js")
    assert re.search(r"\?v=\d+$", url)
    response = client.get(url)
    assert response.status_code == 200
    assert "max-age=31536000" in response.headers["Cache-Control"]


def test_base_page_query_count_stays_small(admin_client):
    # The layout's own queries (user, role, permissions, menus, config,
    # review badge) plus the users list. The layout alone took 7 more
    # before it shared them per request; don't let it creep back up.
    response = admin_client.get("/users/")
    assert response.status_code == 200
    assert _query_count(response) <= 9


def test_system_config_is_reread_on_each_request(app, admin_client):
    admin_client.get("/users/")
    with app.app_context():
        get_system_config().session_timeout_minutes = 77
        db.session.commit()
    # The next request's session lifetime comes from the new value, not one
    # cached by the previous request.
    admin_client.get("/users/")
    assert app.permanent_session_lifetime.total_seconds() == 77 * 60


def test_setup_check_fails_cleanly_without_alembic_table(app):
    # The test DB is built by create_all(), so it has no alembic_version.
    with app.app_context():
        assert is_setup_complete() is False
        # Ran on its own connection: the ORM session is still usable.
        User.query.first()
