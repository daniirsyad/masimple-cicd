import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class DeploymentRun(db.Model):
    """One "Deploy" click — covers every manifest sharing group_name if
    triggered on a group, or just one manifest if triggered standalone.
    Same shape/role as BuildBatch.
    """

    __tablename__ = "deployment_runs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Snapshot of what was triggered — null for a standalone-manifest run.
    group_name = db.Column(db.String, nullable=True)
    # "deploy" (kubectl apply, the original/default) or "stop" (kubectl
    # delete — tears down what an earlier "deploy" run applied). Drives
    # which provider method the worker calls (see
    # app/services/deployment/worker.py._run_deployment) and how this run
    # is labeled on /deployment-runs.
    action = db.Column(db.String, default="deploy", nullable=False)
    status = db.Column(db.String, default="queued", nullable=False)
    triggered_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    triggerer = db.relationship("User")
