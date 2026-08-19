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
    # Login lockout: a User's failed_login_attempts counter locks the
    # account once it reaches this value — see User.failed_login_attempts
    # and app/blueprints/auth/routes.py's login(). Read fresh on every
    # login attempt via get_system_config(), same "no restart needed"
    # pattern as commit_log_limit above.
    max_login_attempts = db.Column(db.Integer, nullable=False, default=5)
    # Telegram Bot API integration (app/services/telegram/) — used today for
    # security notifications (wrong password, lockout, login — see
    # security_notification_user_id below) and forgot-password reset links;
    # the bot/chat plumbing here is also what a future workflow-run
    # Telegram/Discord trigger integration would reuse, not built yet.
    # Disabled by default so adding a token alone doesn't start sending
    # messages.
    telegram_notifications_enabled = db.Column(db.Boolean, nullable=False, default=False)
    # Fernet-encrypted via app/utils/crypto.py, same convention as every
    # other stored credential in this app (encrypted_token/
    # encrypted_credentials/encrypted_api_key) — never returned to a
    # template, only decrypted right before an outbound Telegram API call.
    encrypted_telegram_bot_token = db.Column(db.String, nullable=True)
    # The single User whose Telegram chat (User.telegram_chat_id) receives
    # security alerts about EVERY account's wrong-password/lockout/login
    # events — see app.services.telegram.helpers.notify_security_contact().
    # Deliberately not "notify each affected user about their own
    # activity" — a security contact watching every account is the whole
    # point. NULL disables these alerts even if telegram_notifications_enabled
    # is on. Distinct from the forgot-password flow, which always messages
    # the account owner directly via their own telegram_chat_id (only they
    # can act on that link, so it can't be redirected here).
    security_notification_user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    security_notification_user = db.relationship("User", foreign_keys=[security_notification_user_id])
    # Separate from telegram_notifications_enabled above: gates the
    # long-polling background thread (app/services/telegram/worker.py) that
    # lets a linked user run/check Workflows via Telegram bot commands
    # (/run, /status), reusing the same encrypted_telegram_bot_token. Kept
    # as its own toggle so enabling outbound security/reset notifications
    # doesn't also silently start accepting inbound commands, and vice
    # versa. Off by default, same reasoning as telegram_notifications_enabled.
    telegram_bot_commands_enabled = db.Column(db.Boolean, nullable=False, default=False)
    # Telegram's getUpdates offset — the highest update_id already
    # processed. Persisted (not just kept in-memory) so a process restart
    # doesn't re-deliver, and re-execute, already-handled /run commands.
    # NULL means "no updates processed yet".
    telegram_last_update_id = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
