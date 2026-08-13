import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class SystemConfig(db.Model):
    """Singleton row of app-wide settings, edited via the /config page."""

    __tablename__ = "system_configs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timezone = db.Column(db.String, nullable=False, default="UTC")
    session_timeout_minutes = db.Column(db.Integer, nullable=False, default=60)
    build_engine = db.Column(db.String, nullable=False, default="docker")
    # The navbar and sidebar both show the app title ("MASIMPLE CICD") — redundant
    # whenever the sidebar is visible (desktop) since it has its own. When
    # enabled, the navbar's copy is hidden client-side while the sidebar is
    # open (see sidebar.js) so exactly one is ever showing.
    hide_navbar_title_when_sidebar_open = db.Column(db.Boolean, nullable=False, default=True)
    # How often the deployment live-status poller re-checks whether each
    # deployed manifest's resources are still present on their target
    # server(s) — see app/services/deployment/worker.py._status_poll_loop.
    # Re-read fresh every poll cycle so a change here takes effect without a
    # restart (same pattern as session_timeout_minutes in app/__init__.py's
    # _apply_session_timeout).
    deployment_status_check_interval_seconds = db.Column(db.Integer, nullable=False, default=60)
    # Max commits GitProvider.get_commits()/get_commit_messages() reads when
    # there's no prior build to diff against (first build on a branch) or the
    # prior one's commit is no longer reachable (force-push/rebase) — see
    # GitHubProvider.get_commits's DEFAULT_LOG_LIMIT fallback. Read fresh on
    # every call site (app.utils.system_config.get_system_config()), same
    # "no restart needed" pattern as deployment_status_check_interval_seconds.
    commit_log_limit = db.Column(db.Integer, nullable=False, default=20)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
