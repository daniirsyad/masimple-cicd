import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class SystemConfig(db.Model):
    """Singleton row of app-wide settings, edited via the /config page."""

    __tablename__ = "system_configs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timezone = db.Column(db.String, nullable=False, default="UTC")
    session_timeout_minutes = db.Column(db.Integer, nullable=False, default=60)
    build_engine = db.Column(db.String, nullable=False, default="docker")
    # The navbar and sidebar both show the app title ("Hamilton") — redundant
    # whenever the sidebar is visible (desktop) since it has its own. When
    # enabled, the navbar's copy is hidden client-side while the sidebar is
    # open (see sidebar.js) so exactly one is ever showing.
    hide_navbar_title_when_sidebar_open = db.Column(db.Boolean, nullable=False, default=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
