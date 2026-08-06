import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String, nullable=False)
    target_type = db.Column(db.String, nullable=True)
    target_id = db.Column(db.String, nullable=True)
    description = db.Column(db.Text)
    ip_address = db.Column(db.String, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User")
