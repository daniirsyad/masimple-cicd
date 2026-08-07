import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class VersionLink(db.Model):
    __tablename__ = "version_links"
    __table_args__ = (
        db.UniqueConstraint("batch_id", "linked_batch_id", name="uq_version_links_pair"),
        db.CheckConstraint("batch_id != linked_batch_id", name="ck_version_links_not_self"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = db.Column(UUID(as_uuid=True), db.ForeignKey("build_batches.id"), nullable=False)
    linked_batch_id = db.Column(UUID(as_uuid=True), db.ForeignKey("build_batches.id"), nullable=False)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    batch = db.relationship(
        "BuildBatch",
        foreign_keys=[batch_id],
        backref=db.backref("outgoing_links", lazy="dynamic"),
    )
    linked_batch = db.relationship(
        "BuildBatch",
        foreign_keys=[linked_batch_id],
        backref=db.backref("incoming_links", lazy="dynamic"),
    )
