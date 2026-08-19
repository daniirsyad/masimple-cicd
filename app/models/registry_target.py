import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class RegistryTarget(db.Model):
    __tablename__ = "registry_targets"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, nullable=False)
    provider_type = db.Column(db.String, default="dockerhub", nullable=False)
    username = db.Column(db.String, nullable=True)
    encrypted_token = db.Column(db.Text, nullable=True)
    registry_url = db.Column(db.String, nullable=True)
    # Disabled (archived) targets drop off the main list onto a separate
    # Archived page, and stop being offered as a choice for new Builders —
    # see registries.routes.disable/enable and builders.routes._registry_choices.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
