import uuid
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    full_name = db.Column(db.String)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    role_id = db.Column(UUID(as_uuid=True), db.ForeignKey("roles.id"))
    created_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)

    role = db.relationship("Role", back_populates="users")
    creator = db.relationship("User", remote_side=[id])

    def has_permission(self, code):
        return self.role is not None and any(p.code == code for p in self.role.permissions)

    def has_role(self, name):
        return self.role is not None and self.role.name == name
