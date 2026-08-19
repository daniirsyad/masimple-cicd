import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

# Individually selected Builders/Manifests for a step — deliberately only
# ever populated with ungrouped ones (enforced in workflows.routes, not
# here): anything with a group_name is only reachable by selecting that
# group via WorkflowStepGroup below, same "no per-row action on a grouped
# item" rule builders/index.html and deployment_manifests/index.html already
# enforce (show_deploy_button=False for grouped rows).
workflow_step_builders = db.Table(
    "workflow_step_builders",
    db.Column("workflow_step_id", UUID(as_uuid=True), db.ForeignKey("workflow_steps.id"), primary_key=True),
    db.Column("builder_id", UUID(as_uuid=True), db.ForeignKey("builders.id"), primary_key=True),
)

workflow_step_manifests = db.Table(
    "workflow_step_manifests",
    db.Column("workflow_step_id", UUID(as_uuid=True), db.ForeignKey("workflow_steps.id"), primary_key=True),
    db.Column("manifest_id", UUID(as_uuid=True), db.ForeignKey("deployment_manifests.id"), primary_key=True),
)

STEP_TYPES = ("build", "deploy")
ON_FAILURE_OPTIONS = ("stop", "continue")


class WorkflowStep(db.Model):
    """One step in a Workflow's ordered sequence — either "build" (a batch of
    existing Builders, same shape as /builders/build's multi-select trigger)
    or "deploy" (a batch of existing DeploymentManifests, same shape as
    /deployment-manifests's Deploy Group/Deploy Selected). Which concrete
    Builders/Manifests a step actually targets is resolved at run time from
    `selected_groups` (live group_name references — see
    app/services/workflow/resolver.py) union `selected_builders`/
    `selected_manifests` (a fixed selection of ungrouped ones), not stored
    here as a frozen list — see Workflow's docstring.
    """

    __tablename__ = "workflow_steps"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = db.Column(UUID(as_uuid=True), db.ForeignKey("workflows.id"), nullable=False)
    # Position within the workflow, user-configurable via drag-and-drop (see
    # workflows.reorder_steps) — same role as DeploymentManifest.order.
    order = db.Column(db.Integer, default=0, nullable=False)
    step_type = db.Column(db.String, nullable=False)
    # "stop" (default): a failed step halts the run, later steps never start.
    # "continue": the run proceeds to the next step anyway; its final status
    # still reflects that a step failed (see WorkflowRun.has_failed_step).
    on_failure = db.Column(db.String, default="stop", nullable=False)
    # Only meaningful for step_type="build". Either filled in by hand at
    # authoring time (the three below are then required, see
    # workflows.routes.add_build_step), or left blank with
    # auto_generate_build_metadata=True, in which case the orchestrator
    # computes them fresh on every run via the same compute_build_prefill()
    # engine the authoring-time preview already uses (see
    # workflow.worker._start_step) — instead of a step capturing them once
    # and replaying the same values on every future run.
    bump_type = db.Column(db.String, nullable=True)
    change_type_id = db.Column(UUID(as_uuid=True), db.ForeignKey("change_types.id"), nullable=True)
    object = db.Column(db.String, nullable=True)
    additional_description = db.Column(db.Text, nullable=True)
    auto_generate_build_metadata = db.Column(db.Boolean, default=False, nullable=False)
    # Only consulted when auto_generate_build_metadata is True: pause the run
    # and let a human review/edit the generated values before they're used
    # (see workflow.worker._start_step and workflows.routes.approve_step_run/
    # reject_step_run), vs. apply them immediately with no human in the loop.
    # Defaults True — same "never apply raw AI output unseen" rule already
    # followed everywhere else AI output touches this app (see
    # app/services/ai/build_prefill.py's own docstring).
    require_review_before_build = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # cascade="all, delete-orphan": deleting a Workflow (only ever allowed
    # once it has no WorkflowRun history — see workflows.routes.delete_workflow)
    # takes its WorkflowSteps with it. The steps' own m2m selections
    # (selected_builders/selected_manifests) are cleaned up automatically by
    # SQLAlchemy's secondary-table handling; selected_groups needs its own
    # cascade (see WorkflowStepGroup).
    workflow = db.relationship(
        "Workflow", backref=db.backref("steps", lazy="dynamic", order_by=order, cascade="all, delete-orphan")
    )
    change_type = db.relationship("ChangeType")
    selected_builders = db.relationship("Builder", secondary=workflow_step_builders)
    selected_manifests = db.relationship("DeploymentManifest", secondary=workflow_step_manifests)
