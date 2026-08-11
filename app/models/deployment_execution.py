import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class DeploymentExecution(db.Model):
    """One manifest x one server inside a DeploymentRun. Same shape/role as
    ImageBuild inside a BuildBatch — including the partial unique index that
    enforces "only one deployment execution runs at a time system-wide" for
    the deployment worker's own, independent queue (see
    app/services/deployment/worker.py; this is a separate queue/index from
    ImageBuild's, so a deploy and a build may run concurrently).
    """

    __tablename__ = "deployment_executions"
    __table_args__ = (
        db.Index(
            "ix_deployment_executions_single_running",
            "status",
            unique=True,
            postgresql_where=db.text("status = 'running'"),
        ),
    )

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = db.Column(UUID(as_uuid=True), db.ForeignKey("deployment_runs.id"), nullable=False)
    # Nullable so deleting a DeploymentManifest can detach its history
    # instead of being blocked by it or cascading the delete — mirrors
    # ActivityLog.user_id's nulling on a hard user delete (see
    # deployment_manifests.routes.delete_manifest). A null manifest_id means
    # "this execution's manifest was deleted"; the run/execution row itself,
    # its log, and its rendered_yaml survive for audit.
    manifest_id = db.Column(UUID(as_uuid=True), db.ForeignKey("deployment_manifests.id"), nullable=True)
    server_id = db.Column(UUID(as_uuid=True), db.ForeignKey("deployment_servers.id"), nullable=False)
    # Only set on a "stop"-action execution (run.action == "stop") — points
    # back at the "deploy"-action execution being torn down, so the worker
    # deletes exactly the rendered_yaml that was actually applied rather
    # than re-resolving placeholders (which may have since moved on).
    source_execution_id = db.Column(UUID(as_uuid=True), db.ForeignKey("deployment_executions.id"), nullable=True)
    # What each placeholder key actually resolved to, e.g.
    # "default=DEV.1.2.3.220726105433" or multiple joined with "; " for a
    # manifest with several {{SYS:VERSION:key}} placeholders.
    resolved_version_string = db.Column(db.Text, nullable=True)
    # Snapshot of the manifest's YAML with every placeholder substituted, for
    # audit — never mutated after the fact even if the manifest itself is
    # later edited.
    rendered_yaml = db.Column(db.Text, nullable=True)
    # "queued" / "running" / "success" / "failed" / "skipped" — "skipped" is
    # only ever set on remaining queued executions of a run once another
    # execution in that same run has failed (see worker._update_run_status),
    # implementing "abort remaining on first failure" for a group deploy.
    status = db.Column(db.String, default="queued", nullable=False)
    log = db.Column(db.Text, nullable=True)
    # Live-status poller's last look, only ever set/refreshed on whichever
    # execution is currently the latest for its (manifest_id, server_id)
    # pair — see worker.get_current_deployment / worker._status_poll_loop.
    # "live" / "missing" / "unknown" (provider can't tell, e.g. an "api"
    # server); null until the poller has checked at least once.
    live_status = db.Column(db.String, nullable=True)
    live_checked_at = db.Column(db.DateTime, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    heartbeat_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    run = db.relationship("DeploymentRun", backref=db.backref("executions", lazy="dynamic"))
    manifest = db.relationship("DeploymentManifest", backref=db.backref("executions", lazy="dynamic"))
    server = db.relationship("DeploymentServer", backref=db.backref("executions", lazy="dynamic"))
    source_execution = db.relationship("DeploymentExecution", remote_side=[id])
