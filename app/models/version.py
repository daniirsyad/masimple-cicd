import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class Version(db.Model):
    __tablename__ = "versions"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, unique=True, nullable=False)
    version_type_id = db.Column(UUID(as_uuid=True), db.ForeignKey("version_types.id"), nullable=False)
    major = db.Column(db.Integer, default=0, nullable=False)
    minor = db.Column(db.Integer, default=0, nullable=False)
    patch = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    version_type = db.relationship("VersionType")
