import uuid

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class ChangeType(db.Model):
    __tablename__ = "change_types"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
