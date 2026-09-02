import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db

deployment_manifest_servers = db.Table(
    "deployment_manifest_servers",
    db.Column("manifest_id", UUID(as_uuid=True), db.ForeignKey("deployment_manifests.id"), primary_key=True),
    db.Column("server_id", UUID(as_uuid=True), db.ForeignKey("deployment_servers.id"), primary_key=True),
)

deployment_manifest_users = db.Table(
    "deployment_manifest_users",
    db.Column("manifest_id", UUID(as_uuid=True), db.ForeignKey("deployment_manifests.id"), primary_key=True),
    db.Column("user_id", UUID(as_uuid=True), db.ForeignKey("users.id"), primary_key=True),
)


class DeploymentManifest(db.Model):
    __tablename__ = "deployment_manifests"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String, nullable=False)
    yaml_content = db.Column(db.Text, nullable=False)
    # Purely organizational — powers the /deployment-manifests page's "Deploy
    # Group" button grouping, same role as Builder.group_name. A manifest
    # with no group_name is standalone; one with a group_name deploys
    # together with every other manifest sharing that name.
    group_name = db.Column(db.String, nullable=True)
    # Position within its group_name, user-configurable via drag-and-drop
    # (see deployment_manifests.reorder_manifests). Deploying a group walks
    # manifests in ascending order; stopping a group walks them in
    # descending order (tear down in reverse of how they went up).
    order = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String, default="active", nullable=False)
    # Disabled (archived) manifests drop off the main list onto a separate
    # Archived page, and stop being deployable/selectable for new build-
    # step authoring — see deployment_manifests.routes.disable/enable, its
    # deploy()/update()/restart() guards (never stop() — an archived
    # manifest that's still deployed must still be stoppable), and
    # app/services/workflow/resolver.py's resolve_step_manifests(), which
    # drops a disabled manifest from group resolution on every future
    # workflow run, not just new step authoring.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Total seconds Deploy/Update/Restart will block after a successful
    # `kubectl apply`, polling `kubectl rollout status` until every
    # Deployment/StatefulSet/DaemonSet in this manifest reports Ready,
    # before the action counts as a success (see
    # app/services/deployment/kubernetes_provider.py's
    # apply()/_wait_for_rollout()). 0 skips the wait entirely — an escape
    # hatch for a workload with a known-flaky readiness probe. Lives here,
    # not on a global SystemConfig, since it needs to travel automatically
    # with both manual triggers and Workflow-resolved manifests (see
    # app/services/deployment/worker.py._run_deployment) with no extra
    # plumbing — different manifests have very different startup times.
    # NOTE: this wait runs synchronously inside the deploy worker, holding
    # the system-wide single-flight deploy slot
    # (ix_deployment_executions_single_running) for its full duration —
    # every other queued deploy is blocked until it finishes or times out.
    # Capped at 1800s by the form's NumberRange validator for exactly that
    # reason.
    wait_for_ready_timeout_seconds = db.Column(db.Integer, default=300, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    target_servers = db.relationship(
        "DeploymentServer", secondary=deployment_manifest_servers, backref=db.backref("manifests", lazy="dynamic")
    )
    # Users allowed to deploy/update/stop/restart this specific manifest,
    # in addition to whoever holds deployment_manifest.manage (which always
    # grants full access). Deliberately per-USER, not per-role (unlike
    # DeploymentServer.allowed_roles) — per explicit request.
    #
    # Semantics differ from DeploymentServer.allowed_roles on purpose: an
    # EMPTY list there means "locked to deployment_server.manage users
    # only"; here it means "unrestricted — anyone holding the relevant
    # deployment.* permission may act on it". Matching DeploymentServer's
    # locked-by-default behavior would have silently locked every
    # already-existing manifest out from under whoever was using it the
    # moment this shipped, which no server-provisioning workflow was ever
    # asking for. Restriction here is opt-in: only once a manifest actually
    # has allowed_users assigned does the check start narrowing.
    allowed_users = db.relationship(
        "User", secondary=deployment_manifest_users, backref=db.backref("allowed_deployment_manifests", lazy="dynamic")
    )

    def is_accessible_to(self, user):
        if user.has_permission("deployment_manifest.manage"):
            return True
        if not self.allowed_users:
            return True
        return user in self.allowed_users
