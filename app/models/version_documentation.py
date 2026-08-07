import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class VersionDocumentation(db.Model):
    __tablename__ = "version_documentations"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = db.Column(
        UUID(as_uuid=True), db.ForeignKey("build_batches.id"), unique=True, nullable=False
    )
    built_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    change_type_id = db.Column(UUID(as_uuid=True), db.ForeignKey("change_types.id"), nullable=True)
    object = db.Column(db.String, nullable=True)
    ai_description = db.Column(db.Text, nullable=True)
    ai_provider_used = db.Column(db.String, nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    batch = db.relationship("BuildBatch", backref=db.backref("documentation", uselist=False))
    change_type = db.relationship("ChangeType")
    built_by_user = db.relationship("User", foreign_keys=[built_by])

    @property
    def is_complete(self):
        # Per spec: "Treat a record as incomplete until change type is set" —
        # narrower than the old three-field (type/object/description) check.
        return bool(self.change_type_id)
