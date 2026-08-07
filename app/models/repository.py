import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class Repository(db.Model):
    __tablename__ = "repositories"
    __table_args__ = (
        db.UniqueConstraint("git_source_id", "full_name", name="uq_repositories_source_full_name"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    git_source_id = db.Column(UUID(as_uuid=True), db.ForeignKey("git_sources.id"), nullable=False)
    full_name = db.Column(db.String, nullable=False)
    local_path = db.Column(db.String, nullable=False)
    default_branch = db.Column(db.String, nullable=True)
    status = db.Column(db.String, default="cloning", nullable=False)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    git_source = db.relationship("GitSource", backref=db.backref("repositories", lazy="dynamic"))
