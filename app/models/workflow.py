import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

workflow_roles = db.Table(
    "workflow_roles",
    db.Column("workflow_id", UUID(as_uuid=True), db.ForeignKey("workflows.id"), primary_key=True),
    db.Column("role_id", UUID(as_uuid=True), db.ForeignKey("roles.id"), primary_key=True),
)


class Workflow(db.Model):
    """An ordered, reusable sequence of build/deploy steps — see WorkflowStep.
    Never holds build/deploy config of its own; every step only ever
    references an existing Builder/DeploymentManifest (individually or by
    group_name), same "compose what already exists" principle as a
    DeploymentManifest's version bindings referencing an existing Builder.
    """

    __tablename__ = "workflows"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    creator = db.relationship("User")
    # Roles allowed to view/run this Workflow when the current user lacks
    # workflow.manage (which always grants full access to every Workflow) —
    # same empty-means-locked-to-manage-users semantics as Builder.allowed_roles.
    allowed_roles = db.relationship(
        "Role", secondary=workflow_roles, backref=db.backref("allowed_workflows", lazy="dynamic")
    )

    def is_accessible_to(self, user):
        if user.has_permission("workflow.manage"):
            return True
        return user.role is not None and user.role in self.allowed_roles
