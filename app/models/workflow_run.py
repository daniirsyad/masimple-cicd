import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class WorkflowRun(db.Model):
    """One "Run" click on a Workflow — advances through that Workflow's
    WorkflowSteps in order, one WorkflowStepRun at a time (see that model
    and app/services/workflow/worker.py, the orchestrator that drives this
    forward). Same "one row per trigger" role as BuildBatch/DeploymentRun,
    but unlike those, a WorkflowRun doesn't do any work itself — it only
    tracks which step is current and enqueues real BuildBatch/DeploymentRun
    rows for the underlying build/deploy workers to execute.
    """

    __tablename__ = "workflow_runs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = db.Column(UUID(as_uuid=True), db.ForeignKey("workflows.id"), nullable=False)
    # "queued" (not yet started) / "running" / "success" / "failed" /
    # "completed_with_failures" (only reachable via an on_failure="continue"
    # step — see workflow.worker._advance).
    status = db.Column(db.String, default="queued", nullable=False)
    triggered_by = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    current_step_id = db.Column(UUID(as_uuid=True), db.ForeignKey("workflow_steps.id"), nullable=True)
    # Set the moment any step in this run fails, regardless of that step's
    # on_failure setting — distinguishes a clean "success" from a
    # "completed_with_failures" that only kept going because on_failure was
    # "continue" on the step(s) that actually failed.
    has_failed_step = db.Column(db.Boolean, default=False, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    workflow = db.relationship("Workflow", backref=db.backref("runs", lazy="dynamic"))
    triggerer = db.relationship("User")
    current_step = db.relationship("WorkflowStep")
