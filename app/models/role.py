import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", UUID(as_uuid=True), db.ForeignKey("roles.id"), primary_key=True),
    db.Column("permission_id", UUID(as_uuid=True), db.ForeignKey("permissions.id"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_system = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    users = db.relationship("User", back_populates="role")
    permissions = db.relationship("Permission", secondary=role_permissions, back_populates="roles")
