import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class GitSource(db.Model):
    __tablename__ = "git_sources"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, nullable=False)
    provider_type = db.Column(db.String, default="github", nullable=False)
    encrypted_token = db.Column(db.Text, nullable=True)
    # Disabled (archived) connections drop off the main list onto a separate
    # Archived page, and can no longer register new Repositories — see
    # git_sources.routes.disable/enable/register_repo.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
