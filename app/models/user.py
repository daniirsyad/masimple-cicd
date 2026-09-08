import uuid
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    full_name = db.Column(db.String)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    role_id = db.Column(UUID(as_uuid=True), db.ForeignKey("roles.id"))
    created_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)
    # Login-lockout tracking (app/blueprints/auth/routes.py's login()):
    # incremented on every wrong-password attempt against this user, reset
    # to 0 on a successful login or a completed password reset. The account
    # is considered locked once this reaches SystemConfig.max_login_attempts
    # — locked_at is set the moment that happens (audit/UI display only, not
    # itself the lock condition) and cleared alongside the counter. Only a
    # user.unlock permission holder (or a successful forgot-password reset)
    # can clear it — there is no auto-expiry.
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_at = db.Column(db.DateTime, nullable=True)
    # Numeric Telegram chat ID — see app/services/telegram/. Two distinct
    # uses: (1) a forgot-password reset link for THIS user is always sent
    # here, since only this user can act on it; (2) if this user is the
    # SystemConfig.security_notification_user_id, wrong-password/lockout/
    # login alerts for EVERY account are also sent here. Admin-entered
    # (obtained by the user messaging the configured bot, or a helper like
    # @userinfobot) since this app has no self-service account page and no
    # public signup.
    telegram_chat_id = db.Column(db.String, nullable=True)
    # Discord snowflake user ID (stored as a string, same as Discord's own
    # API — a snowflake exceeds 32-bit int range). Same two uses and same
    # admin/self-entered provenance as telegram_chat_id above — see
    # app/services/discord/. Unlike a Telegram chat ID, a Discord snowflake
    # is never negative.
    discord_user_id = db.Column(db.String, nullable=True)

    role = db.relationship("Role", back_populates="users")
    creator = db.relationship("User", remote_side=[id])

    def has_permission(self, code):
        return self.role is not None and any(p.code == code for p in self.role.permissions)

    def has_role(self, name):
        return self.role is not None and self.role.name == name
