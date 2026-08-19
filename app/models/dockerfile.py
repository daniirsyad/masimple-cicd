import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class Dockerfile(db.Model):
    """A reusable, app-managed Dockerfile — an alternative to a Builder
    pointing at a Dockerfile checked into its repository. Same "shared,
    independently managed config referenced by Builder" shape as
    RegistryTarget/GitSource. Written out to the repo clone at build time
    (see worker._write_managed_dockerfile) when a Builder's
    dockerfile_source is "managed" rather than "repo".
    """

    __tablename__ = "dockerfiles"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, nullable=False)
    content = db.Column(db.Text, nullable=False)
    # Disabled (archived) Dockerfiles drop off the main list onto a separate
    # Archived page, and stop being offered as a choice for new Builders —
    # see dockerfiles.routes.disable/enable and
    # builders.routes._apply_dockerfile_source.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
