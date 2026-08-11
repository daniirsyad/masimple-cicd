import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class WorkflowStepRun(db.Model):
    """One WorkflowStep's execution within one WorkflowRun — exactly one of
    `batch_id`/`deployment_run_id` is set, pointing at the real BuildBatch or
    DeploymentRun the orchestrator enqueued for this step (see
    app/services/workflow/worker.py._start_step), which the underlying
    build/deploy worker actually executes. `step_order`/`step_type` are
    snapshotted from the WorkflowStep at the moment it started, so editing or
    reordering the Workflow's steps later doesn't rewrite a past run's
    history.
    """

    __tablename__ = "workflow_step_runs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_run_id = db.Column(UUID(as_uuid=True), db.ForeignKey("workflow_runs.id"), nullable=False)
    workflow_step_id = db.Column(UUID(as_uuid=True), db.ForeignKey("workflow_steps.id"), nullable=False)
    step_order = db.Column(db.Integer, nullable=False)
    step_type = db.Column(db.String, nullable=False)
    # "running" / "success" / "failed" — no "queued": a WorkflowStepRun row
    # is only ever created the moment its step actually starts (see
    # workflow.worker._start_step), never ahead of time.
    status = db.Column(db.String, default="running", nullable=False)
    batch_id = db.Column(UUID(as_uuid=True), db.ForeignKey("build_batches.id"), nullable=True)
    deployment_run_id = db.Column(UUID(as_uuid=True), db.ForeignKey("deployment_runs.id"), nullable=True)
    # Set instead of enqueueing anything when a step can't even be started
    # (e.g. its group/item selection resolves to nothing, or a build step's
    # builders don't share one Version) — see workflow.worker._fail_step.
    error = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    run = db.relationship("WorkflowRun", backref=db.backref("step_runs", lazy="dynamic", order_by=step_order))
    step = db.relationship("WorkflowStep")
    batch = db.relationship("BuildBatch")
    deployment_run = db.relationship("DeploymentRun")
