import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

deployment_server_roles = db.Table(
    "deployment_server_roles",
    db.Column("deployment_server_id", UUID(as_uuid=True), db.ForeignKey("deployment_servers.id"), primary_key=True),
    db.Column("role_id", UUID(as_uuid=True), db.ForeignKey("roles.id"), primary_key=True),
)


class DeploymentServer(db.Model):
    __tablename__ = "deployment_servers"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, nullable=False)
    # "kube" — apply via the Kubernetes API using a stored kubeconfig/token.
    # "api" — POST the rendered manifest to a client-side agent endpoint.
    connection_type = db.Column(db.String, nullable=False)
    # kubeconfig blob (kube) or "api_url"+"token" JSON (api) — see
    # app/services/deployment/factory.py for how each provider unpacks this.
    encrypted_credentials = db.Column(db.Text, nullable=True)
    status = db.Column(db.String, default="unverified", nullable=False)
    last_checked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Roles allowed to deploy to this server when the current user lacks
    # deployment_server.manage (which always grants full access to every
    # server). No roles assigned means restricted to deployment_server.manage
    # users only — see is_accessible_to() below. Mirrors Builder.allowed_roles.
    allowed_roles = db.relationship(
        "Role", secondary=deployment_server_roles, backref=db.backref("allowed_deployment_servers", lazy="dynamic")
    )

    def is_accessible_to(self, user):
        if user.has_permission("deployment_server.manage"):
            return True
        return user.role is not None and user.role in self.allowed_roles
