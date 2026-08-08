import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class DeploymentManifestVersionBinding(db.Model):
    """One row per {{SYS:VERSION}} / {{SYS:VERSION:key}} placeholder configured
    on a manifest — which Builder's image that placeholder resolves against,
    and optionally a specific past ImageBuild pinned instead of "latest
    successful". Resolved at deploy time by app/services/deployment/resolver.py,
    never at save time.
    """

    __tablename__ = "deployment_manifest_version_bindings"
    __table_args__ = (
        db.UniqueConstraint("manifest_id", "placeholder_key", name="uq_deployment_binding_manifest_key"),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manifest_id = db.Column(UUID(as_uuid=True), db.ForeignKey("deployment_manifests.id"), nullable=False)
    placeholder_key = db.Column(db.String, default="default", nullable=False)
    builder_id = db.Column(UUID(as_uuid=True), db.ForeignKey("builders.id"), nullable=False)
    # If null, resolves to this builder's latest successful ImageBuild at
    # deploy time; if set, always resolves to this specific past build
    # (also how a "rollback" is done — re-deploy with this pinned).
    pinned_image_build_id = db.Column(UUID(as_uuid=True), db.ForeignKey("image_builds.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    manifest = db.relationship("DeploymentManifest", backref=db.backref("version_bindings", lazy="dynamic"))
    builder = db.relationship("Builder")
    pinned_image_build = db.relationship("ImageBuild")
