import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class HelmChartUpload(db.Model):
    """One uploaded chart archive (.tgz) for a Helm manifest whose chart
    source is "upload". Every version is kept, so redeploying an older one
    is how an uploaded chart rolls back. The manifest deploys the upload
    whose chart_version equals its helm_chart_version (unique per manifest),
    so the manifest needs no foreign key back to this table.

    Stored in Postgres rather than on disk: charts are small (capped at
    MAX_CHART_UPLOAD_BYTES), and this way they survive container rebuilds
    and are backed up with everything else.
    """

    __tablename__ = "helm_chart_uploads"
    __table_args__ = (db.UniqueConstraint("manifest_id", "chart_version", name="uq_helm_chart_upload_version"),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manifest_id = db.Column(UUID(as_uuid=True), db.ForeignKey("deployment_manifests.id"), nullable=False, index=True)
    # Read from the archive's Chart.yaml, never typed in.
    chart_name = db.Column(db.String, nullable=False)
    chart_version = db.Column(db.String, nullable=False)
    filename = db.Column(db.String, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    manifest = db.relationship(
        "DeploymentManifest",
        backref=db.backref("helm_chart_uploads", lazy="dynamic", order_by="HelmChartUpload.uploaded_at.desc()"),
    )
