import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class ImageBuild(db.Model):
    __tablename__ = "image_builds"
    __table_args__ = (
        db.Index(
            "ix_image_builds_single_running",
            "status",
            unique=True,
            postgresql_where=db.text("status = 'running'"),
        ),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = db.Column(UUID(as_uuid=True), db.ForeignKey("build_batches.id"), nullable=False)
    builder_id = db.Column(UUID(as_uuid=True), db.ForeignKey("builders.id"), nullable=False)
    branch_used = db.Column(db.String, nullable=False)
    status = db.Column(db.String, default="queued", nullable=False)
    registry_name = db.Column(db.String, nullable=True)
    image_tag = db.Column(db.String, nullable=True)
    image_size = db.Column(db.BigInteger, nullable=True)
    build_log = db.Column(db.Text, nullable=True)
    queue_position = db.Column(db.Integer, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    batch = db.relationship("BuildBatch", backref=db.backref("image_builds", lazy="dynamic"))
    builder = db.relationship("Builder", backref=db.backref("image_builds", lazy="dynamic"))
