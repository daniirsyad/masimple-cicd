import uuid

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class Menu(db.Model):
    __tablename__ = "menus"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label = db.Column(db.String, nullable=False)
    icon = db.Column(db.String, nullable=True)
    url = db.Column(db.String, nullable=True)
    parent_id = db.Column(UUID(as_uuid=True), db.ForeignKey("menus.id"), nullable=True)
    permission_code = db.Column(db.String, nullable=True)
    order = db.Column(db.Integer, default=0, nullable=False)
    show_in_navbar = db.Column(db.Boolean, default=False, nullable=False)
    show_in_sidebar = db.Column(db.Boolean, default=True, nullable=False)

    children = db.relationship("Menu", backref=db.backref("parent", remote_side=[id]))
