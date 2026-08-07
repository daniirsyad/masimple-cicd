import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class ErrorLog(db.Model):
    __tablename__ = "error_logs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    # Where the error happened — a dotted "blueprint.view_function" for
    # request-triggered errors, or a plain module/function name for
    # background work (e.g. the build worker) that has no request context.
    source = db.Column(db.String, nullable=False)
    message = db.Column(db.Text, nullable=False)
    traceback = db.Column(db.Text, nullable=True)
    method = db.Column(db.String, nullable=True)
    path = db.Column(db.String, nullable=True)
    ip_address = db.Column(db.String, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User")
