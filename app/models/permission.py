import uuid

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db
from app.models.role import role_permissions


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = db.Column(db.String, unique=True, nullable=False)
    description = db.Column(db.String)

    roles = db.relationship("Role", secondary=role_permissions, back_populates="permissions")
