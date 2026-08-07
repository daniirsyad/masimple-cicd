import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class AIProviderConfig(db.Model):
    __tablename__ = "ai_provider_configs"
    __table_args__ = (
        db.Index(
            "ix_ai_provider_configs_single_default",
            "is_default",
            unique=True,
            postgresql_where=db.text("is_default = true"),
        ),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_type = db.Column(db.String, nullable=False)
    model_name = db.Column(db.String, nullable=False)
    endpoint_url = db.Column(db.String, nullable=True)
    encrypted_api_key = db.Column(db.Text, nullable=True)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
