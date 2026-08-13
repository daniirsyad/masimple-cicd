import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

build_batch_objects = db.Table(
    "build_batch_objects",
    db.Column("batch_id", UUID(as_uuid=True), db.ForeignKey("build_batches.id"), primary_key=True),
    db.Column("object_id", UUID(as_uuid=True), db.ForeignKey("objects.id"), primary_key=True),
)


class BuildBatch(db.Model):
    __tablename__ = "build_batches"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id = db.Column(UUID(as_uuid=True), db.ForeignKey("versions.id"), nullable=False)
    # Null until the worker actually claims this batch's first ImageBuild —
    # the version bump itself is deferred to that point too (see
    # worker._claim_next_job), so a batch that fails outright never leaves
    # the Version's numbers bumped for nothing.
    full_version_string = db.Column(db.String, nullable=True)
    bump_type = db.Column(db.String, nullable=False)
    # Version.major/minor/patch immediately before this batch's bump —
    # captured at the same time as the bump itself, so a total failure can
    # safely restore them (see worker._update_batch_status). Null until bumped.
    bumped_from_major = db.Column(db.Integer, nullable=True)
    bumped_from_minor = db.Column(db.Integer, nullable=True)
    bumped_from_patch = db.Column(db.Integer, nullable=True)
    requested_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.String, default="queued", nullable=False)
    change_type_id = db.Column(UUID(as_uuid=True), db.ForeignKey("change_types.id"), nullable=True)
    additional_description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    version = db.relationship("Version", backref=db.backref("batches", lazy="dynamic"))
    requester = db.relationship("User")
    change_type = db.relationship("ChangeType")
    # Staged at trigger time (see builders.routes.build()); only copied into a
    # real VersionDocumentation row if/when this batch fully succeeds — a
    # batch that fails is never documented, so these otherwise just sit here
    # unused for its lifetime.
    objects = db.relationship("Object", secondary=build_batch_objects, backref=db.backref("batches", lazy="dynamic"))
