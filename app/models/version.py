import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

# Which Versions a batch's "Linked Batches" picker on the Documentation page
# may offer from, beyond batches sharing the same Version (always allowed —
# see documentation.routes._linked_batch_choices). One-directional: linking
# A to B (from A's own edit form) only ever writes the (A, B) row — it does
# not grant the reverse. B's own Linked Versions picker, and B's own
# Documentation pages, are unaffected unless B is separately edited to add A
# (see versions.routes._sync_linked_versions). Table name distinct from the
# existing `version_links` table (VersionLink: batch-to-batch links) to
# avoid a collision.
version_version_links = db.Table(
    "version_version_links",
    db.Column("version_id", UUID(as_uuid=True), db.ForeignKey("versions.id"), primary_key=True),
    db.Column("linked_version_id", UUID(as_uuid=True), db.ForeignKey("versions.id"), primary_key=True),
)


class Version(db.Model):
    __tablename__ = "versions"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, unique=True, nullable=False)
    version_type_id = db.Column(UUID(as_uuid=True), db.ForeignKey("version_types.id"), nullable=False)
    major = db.Column(db.Integer, default=0, nullable=False)
    minor = db.Column(db.Integer, default=0, nullable=False)
    patch = db.Column(db.Integer, default=0, nullable=False)
    # Disabled (archived) versions drop off the main /versions list onto a
    # separate Archived page, and stop being offered as a choice for new
    # Builders — see versions.routes.disable_version/enable_version and
    # builders.routes._version_choices.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    version_type = db.relationship("VersionType")
    linked_versions = db.relationship(
        "Version",
        secondary=version_version_links,
        primaryjoin=id == version_version_links.c.version_id,
        secondaryjoin=id == version_version_links.c.linked_version_id,
    )
