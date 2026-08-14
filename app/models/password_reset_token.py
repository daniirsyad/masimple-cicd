import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class PasswordResetToken(db.Model):
    """A single-use, expiring token issued by the "forgot password" flow
    (app/blueprints/auth/routes.py's forgot_password()/reset_password()).
    Only `token_hash` (sha256 of the raw token sent via Telegram) is ever
    stored — the raw token itself only exists in the outbound Telegram
    message and the reset link, never in the database, same principle as
    this app never storing a plaintext password.
    """

    __tablename__ = "password_reset_tokens"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    token_hash = db.Column(db.String, nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User")

    def is_valid(self):
        return self.used_at is None and self.expires_at > datetime.utcnow()
