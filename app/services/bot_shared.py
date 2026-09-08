"""Provider-agnostic pieces shared by the Telegram (app/services/telegram/)
and Discord (app/services/discord/) chat-bot integrations: DB/business
logic with no notifier calls and no provider-specific message/keyboard
formatting, so neither provider duplicates it. Both providers' worker.py
import the action tables and group-resolution helpers below verbatim so
their `action:payload`-style dispatch (Telegram's callback_data, Discord's
custom_id/select value) stays behaviorally identical; both providers'
helpers.py import the notification dedup-guard helpers at the bottom.
"""
import hashlib

from app.models import Builder, DeploymentManifest, WorkflowStepRun
from app.services.build.prefill import compute_build_prefill
from app.services.build.versioning import BUMP_TYPES

# Shared spec for the four Deployment Manifest trigger actions — mirrors
# deployment_manifests.routes._trigger_deploy_action /
# _trigger_teardown_style_action, just table-driven instead of near-
# identical route functions. "teardown": True means "only act on
# (manifest, server) pairs that are actually currently deployed, don't
# check is_active" (stop/restart); False means "check is_active first, act
# on every target server unconditionally" (deploy/update — an update IS a
# deploy, see enqueue_deployment_run's own docstring).
DEPLOY_ACTIONS = {
    "deploy": {"permission": "deployment.deploy", "activity": "TRIGGER_DEPLOYMENT_RUN", "verb": "deployment", "teardown": False},
    "update": {"permission": "deployment.update", "activity": "TRIGGER_DEPLOYMENT_UPDATE", "verb": "update", "teardown": False},
    "stop": {"permission": "deployment.stop", "activity": "TRIGGER_DEPLOYMENT_STOP", "verb": "stop", "teardown": True},
    "restart": {"permission": "deployment.restart", "activity": "TRIGGER_DEPLOYMENT_RESTART", "verb": "restart", "teardown": True},
}

# A whole-group action's payload can't embed every member manifest's UUID
# (Telegram's 64-byte callback_data limit, and kept the same for Discord's
# custom_id/value for wire-format parity) — a short, deterministic hash of
# the group name stands in instead, resolved back to actual manifests at
# callback/interaction time via _manifests_in_group. Collision risk is
# negligible at this app's scale (a handful of groups, not thousands).
GROUP_ACTION_PREFIX = {"deploy": "gdeploy", "update": "gupdate", "stop": "gstop", "restart": "grestart"}
GROUP_ACTION_LOOKUP = {prefix: base for base, prefix in GROUP_ACTION_PREFIX.items()}

BUILD_GROUP_ACTIONS = {"gbuild", "gbconfirm", "gbcancel"}


def _group_hash(group_name):
    return hashlib.sha1(group_name.encode()).hexdigest()[:16]


def _target_manifests(active_only):
    query = DeploymentManifest.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(DeploymentManifest.name).all()


def _accessible_with_servers(manifests, user):
    """Manifests this user can act on and that actually have somewhere to
    act on — same two checks _enforce_trigger_access
    (deployment_manifests.routes) makes at trigger time, applied here up
    front so a /deploy-style list doesn't even offer a choice that would
    just 403 or "no target servers" on tap.
    """
    result = []
    for manifest in manifests:
        if not manifest.target_servers:
            continue
        if not manifest.is_accessible_to(user):
            continue
        if any(not server.is_accessible_to(user) for server in manifest.target_servers):
            continue
        result.append(manifest)
    return result


def _currently_live(manifests):
    # Imported here, not at module level: app.services.deployment.worker
    # itself imports app.services.telegram.helpers (for notify_deploy_*),
    # which imports this module — a top-level import here would be a
    # circular import. Deferred to call time instead, same fix pattern as
    # any other genuine import cycle in this codebase.
    from app.services.deployment.worker import is_currently_deployed

    return [
        manifest
        for manifest in manifests
        if any(is_currently_deployed(manifest.id, server.id) for server in manifest.target_servers)
    ]


def _groups_among(manifests):
    """{group_name: [manifest, ...]} — only groups with more than one
    member here, since a single-member "group" button/option would just
    duplicate that manifest's own individual entry.
    """
    groups = {}
    for manifest in manifests:
        if manifest.group_name:
            groups.setdefault(manifest.group_name, []).append(manifest)
    return {name: members for name, members in groups.items() if len(members) > 1}


def _manifests_in_group(group_hash, active_only):
    query = DeploymentManifest.query.filter(DeploymentManifest.group_name.isnot(None))
    if active_only:
        query = query.filter_by(is_active=True)
    manifests = query.all()
    matched_name = next((m.group_name for m in manifests if _group_hash(m.group_name) == group_hash), None)
    if matched_name is None:
        return [], None
    return [m for m in manifests if m.group_name == matched_name], matched_name


def _single_version_groups_among(builders):
    """Same _groups_among (>1 eligible member) plus the one extra constraint
    a group *build* has that a group deploy doesn't: every member must share
    one Version — builders.routes.build() rejects a mixed-Version selection
    outright, so a group whose members disagree never gets a "Whole group"
    entry here (it would just fail on tap).
    """
    groups = _groups_among(builders)
    return {name: members for name, members in groups.items() if len({b.version_id for b in members}) == 1}


def _builders_in_group(group_hash):
    builders = [
        builder
        for builder in Builder.query.filter(Builder.group_name.isnot(None), Builder.is_active.is_(True)).all()
        if builder.default_branch
    ]
    matched_name = next((b.group_name for b in builders if _group_hash(b.group_name) == group_hash), None)
    if matched_name is None:
        return [], None
    return [b for b in builders if b.group_name == matched_name], matched_name


def _build_prefill_for(builder):
    return compute_build_prefill([(builder, builder.default_branch)], additional_description="")


def _group_build_prefill_for(builders):
    return compute_build_prefill([(builder, builder.default_branch) for builder in builders], additional_description="")


def _build_is_ready(prefill):
    has_objects = bool(prefill["matched_objects"]) or any((name or "").strip() for name in prefill["new_object_names"])
    return bool(prefill["bump_type"] in BUMP_TYPES and has_objects and prefill["change_type_id"])


def resolve_group_build_target(user, group_hash):
    """Shared resolve+validate for both the preview and confirm group-build
    interactions: group still exists, every member still accessible, and
    every member still shares one Version (builders.routes.build()'s own
    hard requirement — a mixed-Version group can never actually build).

    Returns (builders, group_name, None) on success, or (None, None,
    error_message) on failure — unlike Telegram's original
    _resolve_group_build_target, this takes no notifier/callback_query_id
    and never replies itself, so each provider's caller sends
    `error_message` back to the user its own way and returns.
    """
    builders, group_name = _builders_in_group(group_hash)
    if not builders:
        return None, None, "That group is no longer available."
    if any(not builder.is_accessible_to(user) for builder in builders):
        return None, None, "You don't have access to one or more builders in this group."
    if len({builder.version_id for builder in builders}) > 1:
        return (
            None,
            None,
            "Builders in this group no longer share one Version — build them individually or from the web UI.",
        )
    return builders, group_name, None


def format_review_summary(step_run):
    """Plain-text rendering of an awaiting_review WorkflowStepRun's
    AI-suggested build values — shared by both providers' push
    notifications and their /review detail views, so all three show the
    same thing the web review panel does (see workflows/run.html's review
    form, pre-filled from these same suggested_* columns).
    """
    change_type_name = step_run.suggested_change_type.name if step_run.suggested_change_type else None
    return (
        f'Workflow "{step_run.run.workflow.name}" needs review before building:\n'
        f"Version Bump: {step_run.suggested_bump_type or '(not suggested — set on the web)'}\n"
        f"Change Type: {change_type_name or '(not suggested — set on the web)'}\n"
        f"Object(s): {step_run.suggested_object_names or '(none)'}\n"
        f"Description: {step_run.suggested_description or '(none)'}"
    )


def _is_workflow_driven_batch(batch):
    """True if this BuildBatch was enqueued by a Workflow step
    (app/services/workflow/worker.py._start_step), not triggered directly
    by a human via Builders/Images or a bot's /build. Workflow-driven
    batches are covered by the WorkflowRun-level notify_run_finished
    instead — this stops a single workflow build step from also firing its
    own, redundant build-level notification.
    """
    return WorkflowStepRun.query.filter_by(batch_id=batch.id).first() is not None


def _is_workflow_driven_run(run):
    """Same idea as _is_workflow_driven_batch above, for a DeploymentRun a
    Workflow deploy step enqueued.
    """
    return WorkflowStepRun.query.filter_by(deployment_run_id=run.id).first() is not None


def _deploy_label(run):
    """What to call this DeploymentRun in a notification: its group name if
    it was a group deploy, otherwise the name(s) of the manifest(s)
    involved (a standalone-manifest run has no group_name — see
    DeploymentRun's own docstring).
    """
    if run.group_name:
        return run.group_name
    names = sorted({execution.manifest.name for execution in run.executions if execution.manifest is not None})
    return ", ".join(names) if names else "a manifest"
