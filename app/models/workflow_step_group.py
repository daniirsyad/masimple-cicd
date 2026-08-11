import uuid

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class WorkflowStepGroup(db.Model):
    """One selected Builder.group_name/DeploymentManifest.group_name for a
    WorkflowStep — a live reference, not a snapshot: resolved fresh against
    whichever Builders/Manifests currently share that group_name every time
    the step runs (see app/services/workflow/resolver.py), same as clicking
    "Build Group"/"Deploy Group" would today. A step can select several
    groups at once, alongside individually selected ungrouped items (see
    WorkflowStep.selected_builders/selected_manifests).
    """

    __tablename__ = "workflow_step_groups"
    __table_args__ = (db.UniqueConstraint("workflow_step_id", "group_name", name="uq_workflow_step_group"),)

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_step_id = db.Column(UUID(as_uuid=True), db.ForeignKey("workflow_steps.id"), nullable=False)
    group_name = db.Column(db.String, nullable=False)

    workflow_step = db.relationship(
        "WorkflowStep", backref=db.backref("selected_groups", lazy="dynamic", cascade="all, delete-orphan")
    )
