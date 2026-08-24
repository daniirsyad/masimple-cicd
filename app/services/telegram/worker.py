"""Telegram bot command handling — long-polls Telegram's getUpdates and lets
a linked User (User.telegram_chat_id) run and check Workflows straight from
Telegram's own "/" command menu (registered via set_my_commands below), the
same permission (`workflow.run`) and accessibility rule
(Workflow.is_accessible_to) the web UI's Run button already enforces.

Also lets that same linked User trigger a manual (non-Workflow) Build
(`/build`) or Deployment Manifest Deploy/Update/Stop/Restart
(`/deploy`/`/update`/`/stop`/`/restart`) directly — the same permission
codes and accessibility/is_active rules the Builders/Deployment Manifests
web routes enforce (builders.routes.build, deployment_manifests.routes.*),
just reached via a callback instead of a form. Build has no Telegram form
to collect Bump Type/Object(s)/Change Type, so it reuses
services.build.prefill.compute_build_prefill (the same "Preview from Git"
engine the web trigger modal uses) and only allows a one-tap build when
every required field resolved confidently — otherwise it points back to
the web UI, the same escape hatch /review already uses for build-metadata
approval.

Independent poll thread from the build/deploy/workflow workers — same
"start once, TESTING/reloader-parent guarded, per-iteration try/except"
shape as app/services/workflow/worker.py.
"""
import hashlib
import threading
import time
import uuid

from app.extensions import db
from app.models import (
    Builder,
    ChangeType,
    DeploymentManifest,
    Object,
    User,
    Version,
    Workflow,
    WorkflowRun,
    WorkflowStepRun,
)
from app.services.build.prefill import compute_build_prefill
from app.services.build.versioning import BUMP_TYPES
from app.services.build.worker import enqueue_build_batch
from app.services.deployment.worker import enqueue_deployment_run, is_currently_deployed
from app.services.telegram.client import TelegramNotifier
from app.services.telegram.helpers import format_review_summary, review_keyboard
from app.services.workflow.worker import approve_awaiting_step, enqueue_workflow_run, reject_awaiting_step
from app.utils.crypto import decrypt
from app.utils.error_logger import log_error
from app.utils.logger import log_activity
from app.utils.runtime import is_werkzeug_reloader_parent
from app.utils.system_config import get_system_config

# Telegram itself holds the connection open up to this long waiting for a
# new update, so an "enabled, nothing to do" tick already blocks for a
# while — this only matters for how quickly a *disabled* config, or a
# just-failed request, gets rechecked.
DISABLED_POLL_INTERVAL_SECONDS = 5
ERROR_RETRY_INTERVAL_SECONDS = 5

RUN_LIST_LIMIT = 10
STATUS_LIST_LIMIT = 5
REVIEW_LIST_LIMIT = 10
BUILD_LIST_LIMIT = 10
MANIFEST_LIST_LIMIT = 10
GROUP_LIST_LIMIT = 10

BOT_COMMANDS = [
    {"command": "start", "description": "Show help"},
    {"command": "run", "description": "Run a workflow"},
    {"command": "status", "description": "Check a workflow run's status"},
    {"command": "review", "description": "Review a build awaiting approval"},
    {"command": "build", "description": "Build an image directly"},
    {"command": "deploy", "description": "Deploy a manifest directly"},
    {"command": "update", "description": "Update a live manifest directly"},
    {"command": "stop", "description": "Stop a live manifest"},
    {"command": "restart", "description": "Restart a live manifest"},
]

HELP_TEXT = (
    "MASIMPLE CICD bot.\n"
    "/run - pick a workflow to run\n"
    "/status - check your recent workflow runs\n"
    "/review - review a build awaiting approval\n"
    "/build - build an image directly (no workflow)\n"
    "/deploy - deploy a manifest directly (no workflow)\n"
    "/update - update a live manifest directly\n"
    "/stop - stop a live manifest\n"
    "/restart - restart a live manifest"
)

_worker_started = False
_worker_lock = threading.Lock()
# Tracks whether set_my_commands has been called since the config was last
# seen enabled — re-registers automatically if an admin disables and later
# re-enables telegram_bot_commands_enabled. Only ever touched from the
# single poll thread, so no lock needed.
_commands_registered = False

# Arbitrary but stable — just needs to not collide with any other advisory
# lock this app might someday take.
TELEGRAM_POLL_LEADER_LOCK_KEY = 917_442_101

# Keeps the leader's advisory-lock connection alive for the process's
# lifetime — see _become_poll_leader.
_leader_lock_connection = None


def _become_poll_leader(app):
    """Blocks until this process holds a Postgres advisory lock making it
    the sole Telegram getUpdates poller app-wide.

    Unlike the build/deploy/workflow workers, which are fine with several
    gunicorn worker processes concurrently polling the same DB-backed queue
    (each claim is a `SELECT ... FOR UPDATE SKIP LOCKED`, so only one wins
    per row), Telegram's getUpdates is a *global* single-consumer long-poll:
    issuing it from more than one process at once gets rejected with a 409
    ("terminated by other getUpdates request"). gunicorn runs multiple
    worker *processes* (see entrypoint.sh), each with its own Python
    interpreter, so the per-process `_worker_started` guard above isn't
    enough — every worker process still starts its own telegram-bot thread.

    Takes the lock on a dedicated connection, detached from the SQLAlchemy
    pool so it's never recycled back in or counted against pool_size, and
    kept open (via the module-level `_leader_lock_connection` reference) for
    the rest of the process's life. `pg_advisory_lock` is session-scoped,
    not transaction-scoped, so it survives the `commit()` below — if this
    process dies, Postgres releases the lock automatically and whichever
    other worker process is blocked here next becomes leader.
    """
    global _leader_lock_connection

    with app.app_context():
        raw_conn = db.engine.raw_connection()
        raw_conn.detach()
        cursor = raw_conn.cursor()
        cursor.execute("SELECT pg_advisory_lock(%s)", (TELEGRAM_POLL_LEADER_LOCK_KEY,))
        cursor.close()
        raw_conn.commit()

    _leader_lock_connection = raw_conn


def _resolve_user(chat_id):
    return User.query.filter_by(telegram_chat_id=str(chat_id)).first()


def _reply(notifier, chat_id, text, reply_markup=None):
    try:
        notifier.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as exc:
        log_error(
            source="telegram.worker.send_message",
            exc=exc,
            description=f"Could not send Telegram reply: {exc}",
        )


def _answer(notifier, callback_query_id, text=None):
    try:
        notifier.answer_callback_query(callback_query_id, text=text)
    except Exception as exc:
        log_error(
            source="telegram.worker.answer_callback_query",
            exc=exc,
            description=f"Could not answer Telegram callback query: {exc}",
        )


def _workflow_keyboard(workflows):
    return {
        "inline_keyboard": [[{"text": workflow.name, "callback_data": f"run:{workflow.id}"}] for workflow in workflows]
    }


def _run_keyboard(runs):
    return {
        "inline_keyboard": [
            [{"text": f"{run.workflow.name} ({run.status})", "callback_data": f"status:{run.id}"}] for run in runs
        ]
    }


# Shared spec for the four Deployment Manifest trigger actions — mirrors
# deployment_manifests.routes._trigger_deploy_action /
# _trigger_teardown_style_action, just table-driven instead of two near-
# identical route functions, since a single generic handler below serves
# both the single-manifest and whole-group callback shapes for all four.
# "teardown": True means "only act on (manifest, server) pairs that are
# actually currently deployed, don't check is_active" (stop/restart);
# False means "check is_active first, act on every target server
# unconditionally" (deploy/update — an update IS a deploy, see
# enqueue_deployment_run's own docstring).
DEPLOY_ACTIONS = {
    "deploy": {"permission": "deployment.deploy", "activity": "TRIGGER_DEPLOYMENT_RUN", "verb": "deployment", "teardown": False},
    "update": {"permission": "deployment.update", "activity": "TRIGGER_DEPLOYMENT_UPDATE", "verb": "update", "teardown": False},
    "stop": {"permission": "deployment.stop", "activity": "TRIGGER_DEPLOYMENT_STOP", "verb": "stop", "teardown": True},
    "restart": {"permission": "deployment.restart", "activity": "TRIGGER_DEPLOYMENT_RESTART", "verb": "restart", "teardown": True},
}

# callback_data for a whole-group action can't embed every member manifest's
# UUID (Telegram's 64-byte limit) — a short, deterministic hash of the group
# name stands in instead, resolved back to actual manifests at callback time
# via _manifests_in_group. Collision risk is negligible at this app's scale
# (a handful of groups, not thousands).
GROUP_ACTION_PREFIX = {"deploy": "gdeploy", "update": "gupdate", "stop": "gstop", "restart": "grestart"}
GROUP_ACTION_LOOKUP = {prefix: base for base, prefix in GROUP_ACTION_PREFIX.items()}


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
    front so the /deploy-style keyboards don't even offer a choice that
    would just 403 or "no target servers" on tap.
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
    return [
        manifest
        for manifest in manifests
        if any(is_currently_deployed(manifest.id, server.id) for server in manifest.target_servers)
    ]


def _groups_among(manifests):
    """{group_name: [manifest, ...]} — only groups with more than one
    member here, since a single-member "group" button would just duplicate
    that manifest's own individual button.
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


def _manifest_keyboard(base_action, manifests, groups):
    rows = [[{"text": manifest.name, "callback_data": f"{base_action}:{manifest.id}"}] for manifest in manifests]
    group_prefix = GROUP_ACTION_PREFIX[base_action]
    for name in sorted(groups)[:GROUP_LIST_LIMIT]:
        rows.append([{"text": f"\U0001F4E6 Whole group: {name}", "callback_data": f"{group_prefix}:{_group_hash(name)}"}])
    return {"inline_keyboard": rows}


def _handle_manifest_list_command(notifier, chat_id, user, base_action):
    spec = DEPLOY_ACTIONS[base_action]
    if not user.has_permission(spec["permission"]):
        _reply(notifier, chat_id, f"You don't have permission to {spec['verb']} manifests.")
        return

    manifests = _accessible_with_servers(_target_manifests(active_only=not spec["teardown"]), user)
    if spec["teardown"]:
        # Stopping/restarting a manifest nothing's currently deployed for is
        # a guaranteed no-op (enqueue_deployment_run skips every pair with
        # nothing live) — filtered out up front so the list isn't full of
        # dead-end taps.
        manifests = _currently_live(manifests)

    if not manifests:
        _reply(notifier, chat_id, f"No manifests available to {spec['verb']}.")
        return

    manifests = manifests[:MANIFEST_LIST_LIMIT]
    groups = _groups_among(manifests)

    _reply(
        notifier,
        chat_id,
        f"Pick a manifest to {spec['verb']} (or a whole group):",
        reply_markup=_manifest_keyboard(base_action, manifests, groups),
    )


def _handle_deploy_command(notifier, chat_id, user):
    _handle_manifest_list_command(notifier, chat_id, user, "deploy")


def _handle_update_command(notifier, chat_id, user):
    _handle_manifest_list_command(notifier, chat_id, user, "update")


def _handle_stop_command(notifier, chat_id, user):
    _handle_manifest_list_command(notifier, chat_id, user, "stop")


def _handle_restart_command(notifier, chat_id, user):
    _handle_manifest_list_command(notifier, chat_id, user, "restart")


BUILD_GROUP_ACTIONS = {"gbuild", "gbconfirm", "gbcancel"}


def _single_version_groups_among(builders):
    """Same _groups_among (>1 eligible member) plus the one extra constraint
    a group *build* has that a group deploy doesn't: every member must share
    one Version — builders.routes.build() rejects a mixed-Version selection
    outright, so a group whose members disagree never gets a "Whole group"
    button here (it would just fail on tap).
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


def _builder_keyboard(builders, groups):
    rows = [[{"text": builder.name, "callback_data": f"build:{builder.id}"}] for builder in builders]
    for name in sorted(groups)[:GROUP_LIST_LIMIT]:
        rows.append([{"text": f"\U0001F4E6 Whole group: {name}", "callback_data": f"gbuild:{_group_hash(name)}"}])
    return {"inline_keyboard": rows}


def _handle_build_command(notifier, chat_id, user):
    if not user.has_permission("builder.build"):
        _reply(notifier, chat_id, "You don't have permission to trigger builds.")
        return

    builders = [
        builder
        for builder in Builder.query.filter_by(is_active=True).order_by(Builder.name).all()
        if builder.default_branch and builder.is_accessible_to(user)
    ][:BUILD_LIST_LIMIT]

    if not builders:
        _reply(notifier, chat_id, "No builders available to build.")
        return

    groups = _single_version_groups_among(builders)

    _reply(
        notifier, chat_id, "Pick a builder to build (or a whole group):", reply_markup=_builder_keyboard(builders, groups)
    )


def _handle_run_command(notifier, chat_id, user):
    if not user.has_permission("workflow.run"):
        _reply(notifier, chat_id, "You don't have permission to run workflows.")
        return

    workflows = [
        workflow
        for workflow in Workflow.query.filter_by(is_active=True).order_by(Workflow.name).all()
        if workflow.is_accessible_to(user)
    ][:RUN_LIST_LIMIT]

    if not workflows:
        _reply(notifier, chat_id, "No workflows available to run.")
        return

    _reply(notifier, chat_id, "Pick a workflow to run:", reply_markup=_workflow_keyboard(workflows))


def _handle_status_command(notifier, chat_id, user):
    runs = (
        WorkflowRun.query.filter_by(triggered_by=user.id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(STATUS_LIST_LIMIT)
        .all()
    )
    if not runs:
        _reply(notifier, chat_id, "No workflow runs yet.")
        return

    _reply(notifier, chat_id, "Pick a run to check:", reply_markup=_run_keyboard(runs))


def _review_keyboard_for_list(step_runs):
    return {
        "inline_keyboard": [
            [
                {
                    "text": f"{step_run.run.workflow.name} (step {step_run.step_order})",
                    "callback_data": f"review:{step_run.id}",
                }
            ]
            for step_run in step_runs
        ]
    }


def _handle_review_command(notifier, chat_id, user):
    # Same authorization as the web review panel
    # (workflows.routes._awaiting_review_step_run_or_404): any workflow.run
    # holder who can see the workflow, not just whoever triggered the run —
    # a shared review queue, unlike /status's "your own runs only" scope.
    if not user.has_permission("workflow.run"):
        _reply(notifier, chat_id, "You don't have permission to review workflow builds.")
        return

    step_runs = [
        step_run
        for step_run in WorkflowStepRun.query.filter_by(status="awaiting_review")
        .order_by(WorkflowStepRun.created_at.desc())
        .all()
        if step_run.run.workflow.is_accessible_to(user)
    ][:REVIEW_LIST_LIMIT]

    if not step_runs:
        _reply(notifier, chat_id, "Nothing awaiting review.")
        return

    _reply(notifier, chat_id, "Pick a build to review:", reply_markup=_review_keyboard_for_list(step_runs))


def _handle_message(notifier, message):
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if not text:
        return
    # Group chats append the bot's own username, e.g. "/run@my_bot".
    command = text.split()[0].split("@")[0].lower()

    user = _resolve_user(chat_id)
    if user is None:
        if command in ("/start", "/run", "/status", "/review", "/build", "/deploy", "/update", "/stop", "/restart"):
            _reply(
                notifier,
                chat_id,
                "Your Telegram account isn't linked. Ask an admin to set your Chat ID in Account Settings.",
            )
        return

    if command == "/start":
        _reply(notifier, chat_id, HELP_TEXT)
    elif command == "/run":
        _handle_run_command(notifier, chat_id, user)
    elif command == "/status":
        _handle_status_command(notifier, chat_id, user)
    elif command == "/review":
        _handle_review_command(notifier, chat_id, user)
    elif command == "/build":
        _handle_build_command(notifier, chat_id, user)
    elif command == "/deploy":
        _handle_deploy_command(notifier, chat_id, user)
    elif command == "/update":
        _handle_update_command(notifier, chat_id, user)
    elif command == "/stop":
        _handle_stop_command(notifier, chat_id, user)
    elif command == "/restart":
        _handle_restart_command(notifier, chat_id, user)
    else:
        _reply(notifier, chat_id, "Unrecognized command. Try /run, /status, /review, /build, /deploy, /update, /stop, or /restart.")


def _handle_run_callback(notifier, callback_query_id, chat_id, user, workflow_id):
    if not user.has_permission("workflow.run"):
        _answer(notifier, callback_query_id, "You don't have permission to run workflows.")
        return

    workflow = Workflow.query.get(workflow_id)
    if workflow is None or not workflow.is_active or not workflow.is_accessible_to(user):
        _answer(notifier, callback_query_id, "That workflow is no longer available.")
        return

    run = enqueue_workflow_run(workflow, triggered_by=user.id)
    log_activity(
        action="RUN_WORKFLOW",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Workflow '{workflow.name}' run via Telegram by {user.username}",
        user=user,
    )
    _answer(notifier, callback_query_id, "Run queued.")
    _reply(notifier, chat_id, f'Queued a run of "{workflow.name}". Use /status to check progress.')


def _format_run_status(run):
    lines = [f'Workflow "{run.workflow.name}" — {run.status}']
    for step_run in run.step_runs.all():
        line = f"  Step {step_run.step_order} ({step_run.step_type}): {step_run.status}"
        if step_run.error:
            line += f" — {step_run.error}"
        lines.append(line)
    return "\n".join(lines)


def _handle_status_callback(notifier, callback_query_id, chat_id, user, run_id):
    # Deliberately narrower than Workflow.is_accessible_to: only a run this
    # user personally triggered, not every run of every workflow they can
    # view — avoids exposing other users' run details through Telegram.
    run = WorkflowRun.query.get(run_id)
    if run is None or run.triggered_by != user.id:
        _answer(notifier, callback_query_id, "Run not found.")
        return

    _answer(notifier, callback_query_id)
    _reply(notifier, chat_id, _format_run_status(run))


def _awaiting_review_step_run_for_user(step_run_id, user):
    """Same rule as workflows.routes._awaiting_review_step_run_or_404: must
    still be awaiting_review (an approve/reject callback tapped twice, or
    tapped after someone else already acted on it, is a no-op not a
    crash), and the reviewer must be able to see the workflow. Returns
    None if either check fails.
    """
    step_run = WorkflowStepRun.query.get(step_run_id)
    if step_run is None or step_run.status != "awaiting_review":
        return None
    if not step_run.run.workflow.is_accessible_to(user):
        return None
    return step_run


def _handle_review_detail_callback(notifier, callback_query_id, chat_id, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _answer(notifier, callback_query_id, "You don't have permission to review workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _answer(notifier, callback_query_id, "This review is no longer pending.")
        return

    _answer(notifier, callback_query_id)
    _reply(notifier, chat_id, format_review_summary(step_run), reply_markup=review_keyboard(step_run))


def _handle_approve_review_callback(notifier, callback_query_id, chat_id, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _answer(notifier, callback_query_id, "You don't have permission to approve workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _answer(notifier, callback_query_id, "This review is no longer pending.")
        return

    # Telegram has no form to collect a missing Version Bump/Change Type
    # the way the web review panel's dropdowns do — approve as-is only
    # when the AI/heuristic suggestion already has both.
    if not step_run.suggested_bump_type or not step_run.suggested_change_type_id:
        _answer(notifier, callback_query_id, "Missing Version Bump/Change Type — approve from the web UI instead.")
        return

    run = step_run.run
    try:
        approve_awaiting_step(
            step_run,
            bump_type=step_run.suggested_bump_type,
            change_type_id=step_run.suggested_change_type_id,
            object_names_text=step_run.suggested_object_names,
            description=step_run.suggested_description,
            requested_by=user.id,
        )
    except ValueError as exc:
        _answer(notifier, callback_query_id, str(exc))
        return

    log_activity(
        action="APPROVE_WORKFLOW_BUILD_STEP",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Approved AI-suggested build metadata for a step in workflow '{run.workflow.name}' via Telegram by {user.username}",
        user=user,
    )
    _answer(notifier, callback_query_id, "Approved.")
    _reply(notifier, chat_id, f'Approved and queued the build for "{run.workflow.name}".')


def _handle_reject_review_callback(notifier, callback_query_id, chat_id, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _answer(notifier, callback_query_id, "You don't have permission to reject workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _answer(notifier, callback_query_id, "This review is no longer pending.")
        return

    run = step_run.run
    reject_awaiting_step(step_run)
    log_activity(
        action="REJECT_WORKFLOW_BUILD_STEP",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Rejected AI-suggested build metadata for a step in workflow '{run.workflow.name}' via Telegram by {user.username}",
        user=user,
    )
    _answer(notifier, callback_query_id, "Rejected.")
    _reply(notifier, chat_id, f'Rejected the build step for "{run.workflow.name}".')


def _handle_manifest_action_callback(notifier, callback_query_id, chat_id, user, base_action, payload, is_group):
    """Shared body for the deploy/update/stop/restart callbacks, both the
    single-manifest (`payload` a manifest UUID) and whole-group (`payload` a
    _group_hash) shapes — re-runs the same checks
    deployment_manifests.routes._trigger_deploy_action /
    _trigger_teardown_style_action make server-side (permission,
    is_active for deploy/update only, manifest + target-server
    accessibility), since a stale button (a manifest disabled/deleted, or
    access revoked, between the list being shown and this tap) must still
    be caught here, not just at list-build time.
    """
    spec = DEPLOY_ACTIONS[base_action]
    if not user.has_permission(spec["permission"]):
        _answer(notifier, callback_query_id, f"You don't have permission to {spec['verb']} manifests.")
        return

    if is_group:
        manifests, group_name = _manifests_in_group(payload, active_only=not spec["teardown"])
        if not manifests:
            _answer(notifier, callback_query_id, "That group is no longer available.")
            return
        manifests.sort(key=lambda m: (m.order, m.name), reverse=spec["teardown"])
    else:
        manifest = DeploymentManifest.query.get(payload)
        if manifest is None:
            _answer(notifier, callback_query_id, "That manifest is no longer available.")
            return
        manifests = [manifest]
        group_name = None

    if not spec["teardown"]:
        disabled = [m.name for m in manifests if not m.is_active]
        if disabled:
            _answer(notifier, callback_query_id, f"Cannot {spec['verb']}: disabled manifest(s) — {', '.join(disabled)}.")
            return

    if any(not m.is_accessible_to(user) for m in manifests):
        _answer(notifier, callback_query_id, "You don't have access to one or more of these manifests.")
        return

    servers = {server for m in manifests for server in m.target_servers}
    if any(not server.is_accessible_to(user) for server in servers):
        _answer(notifier, callback_query_id, "You don't have access to one or more target servers.")
        return
    if not spec["teardown"] and not servers:
        _answer(notifier, callback_query_id, "No target servers configured for this selection.")
        return

    run, execution_count = enqueue_deployment_run(
        manifests=manifests,
        triggered_by=user.id,
        group_name=group_name,
        action=base_action if spec["teardown"] else "deploy",
    )

    if spec["teardown"] and execution_count == 0:
        db.session.delete(run)
        db.session.commit()
        _answer(notifier, callback_query_id, "Nothing currently deployed for this selection.")
        return

    log_activity(
        action=spec["activity"],
        target_type="deployment_run",
        target_id=str(run.id),
        description=(
            f"Triggered a {spec['verb']} run via Telegram by {user.username} "
            f"({len(manifests)} manifest(s), {execution_count} execution(s))"
        ),
        user=user,
    )

    label = group_name or manifests[0].name
    _answer(notifier, callback_query_id, f"{spec['verb'].capitalize()} queued.")
    _reply(notifier, chat_id, f'{spec["verb"].capitalize()} queued for "{label}" ({execution_count} execution(s)).')


def _build_prefill_for(builder):
    return compute_build_prefill([(builder, builder.default_branch)], additional_description="")


def _build_is_ready(prefill):
    has_objects = bool(prefill["matched_objects"]) or any((name or "").strip() for name in prefill["new_object_names"])
    return bool(prefill["bump_type"] in BUMP_TYPES and has_objects and prefill["change_type_id"])


def _format_build_summary(builder, prefill):
    change_type = ChangeType.query.get(prefill["change_type_id"]) if prefill["change_type_id"] else None
    objects = [obj.name for obj in prefill["matched_objects"]] + list(prefill["new_object_names"])
    return (
        f'Builder "{builder.name}" — {prefill["commit_count"]} new commit(s) since the last build.\n'
        f"Bump Type: {prefill['bump_type']}\n"
        f"Object(s): {', '.join(objects) or '(none)'}\n"
        f"Change Type: {change_type.name if change_type else '(none)'}\n"
        f"Description: {prefill['description'] or '(none)'}"
    )


def _build_confirm_keyboard(builder):
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm build", "callback_data": f"bconfirm:{builder.id}"},
                {"text": "❌ Cancel", "callback_data": f"bcancel:{builder.id}"},
            ]
        ]
    }


def _builder_still_buildable(builder, user):
    return builder is not None and builder.is_active and builder.default_branch and builder.is_accessible_to(user)


def _handle_build_callback(notifier, callback_query_id, chat_id, user, builder_id):
    if not user.has_permission("builder.build"):
        _answer(notifier, callback_query_id, "You don't have permission to trigger builds.")
        return

    builder = Builder.query.get(builder_id)
    if not _builder_still_buildable(builder, user):
        _answer(notifier, callback_query_id, "That builder is no longer available.")
        return

    prefill = _build_prefill_for(builder)
    _answer(notifier, callback_query_id)

    if not _build_is_ready(prefill):
        _reply(
            notifier,
            chat_id,
            f'Builder "{builder.name}": couldn\'t confidently determine Bump Type/Object(s)/Change Type from the '
            f"{prefill['commit_count']} new commit(s) since the last build — finish this one from the web UI instead.",
        )
        return

    _reply(notifier, chat_id, _format_build_summary(builder, prefill), reply_markup=_build_confirm_keyboard(builder))


def _handle_build_confirm_callback(notifier, callback_query_id, chat_id, user, builder_id):
    if not user.has_permission("builder.build"):
        _answer(notifier, callback_query_id, "You don't have permission to trigger builds.")
        return

    builder = Builder.query.get(builder_id)
    if not _builder_still_buildable(builder, user):
        _answer(notifier, callback_query_id, "That builder is no longer available.")
        return

    # Recomputed fresh rather than trusting the earlier preview's values —
    # callback_data can't carry the full suggestion (object names/
    # description don't fit Telegram's 64-byte limit), and this is
    # deterministic enough over the few-second gap between preview and
    # confirm that it's not worth persisting a stand-in row just to avoid
    # the recompute (same tradeoff /review avoids entirely by storing its
    # suggestion on the WorkflowStepRun row up front).
    prefill = _build_prefill_for(builder)
    if not _build_is_ready(prefill):
        _answer(notifier, callback_query_id, "No longer resolvable as-is — use the web UI instead.")
        return

    object_ids = [str(obj.id) for obj in prefill["matched_objects"]]
    objects = Object.resolve(object_ids, prefill["new_object_names"])

    batch = enqueue_build_batch(
        version=builder.version,
        bump_type=prefill["bump_type"],
        builder_branches=[(builder, builder.default_branch)],
        objects=objects,
        additional_description="",
        requested_by=user.id,
        change_type_id=prefill["change_type_id"],
    )

    log_activity(
        action="TRIGGER_BUILD_BATCH",
        target_type="build_batch",
        target_id=str(batch.id),
        description=f"Triggered a build batch via Telegram by {user.username} (builder '{builder.name}')",
        user=user,
    )
    _answer(notifier, callback_query_id, "Build queued.")
    _reply(notifier, chat_id, f'Build queued for "{builder.name}".')


def _handle_build_cancel_callback(notifier, callback_query_id, chat_id, user, builder_id):
    _answer(notifier, callback_query_id, "Cancelled.")


def _group_build_prefill_for(builders):
    return compute_build_prefill([(builder, builder.default_branch) for builder in builders], additional_description="")


def _format_group_build_summary(group_name, builders, prefill):
    change_type = ChangeType.query.get(prefill["change_type_id"]) if prefill["change_type_id"] else None
    objects = [obj.name for obj in prefill["matched_objects"]] + list(prefill["new_object_names"])
    builder_names = ", ".join(sorted(builder.name for builder in builders))
    return (
        f'Group "{group_name}" ({builder_names}) — {prefill["commit_count"]} new commit(s) since the last build.\n'
        f"Bump Type: {prefill['bump_type']}\n"
        f"Object(s): {', '.join(objects) or '(none)'}\n"
        f"Change Type: {change_type.name if change_type else '(none)'}\n"
        f"Description: {prefill['description'] or '(none)'}"
    )


def _group_build_confirm_keyboard(group_hash):
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm build", "callback_data": f"gbconfirm:{group_hash}"},
                {"text": "❌ Cancel", "callback_data": f"gbcancel:{group_hash}"},
            ]
        ]
    }


def _resolve_group_build_target(callback_query_id, notifier, user, group_hash):
    """Shared resolve+validate for both the preview and confirm group-build
    callbacks: group still exists, every member still accessible, and every
    member still shares one Version (builders.routes.build()'s own hard
    requirement — a mixed-Version group can never actually build). Returns
    (builders, group_name) on success, (None, None) after already answering
    the callback with why not.
    """
    builders, group_name = _builders_in_group(group_hash)
    if not builders:
        _answer(notifier, callback_query_id, "That group is no longer available.")
        return None, None
    if any(not builder.is_accessible_to(user) for builder in builders):
        _answer(notifier, callback_query_id, "You don't have access to one or more builders in this group.")
        return None, None
    if len({builder.version_id for builder in builders}) > 1:
        _answer(
            notifier,
            callback_query_id,
            "Builders in this group no longer share one Version — build them individually or from the web UI.",
        )
        return None, None
    return builders, group_name


def _handle_group_build_callback(notifier, callback_query_id, chat_id, user, group_hash):
    if not user.has_permission("builder.build"):
        _answer(notifier, callback_query_id, "You don't have permission to trigger builds.")
        return

    builders, group_name = _resolve_group_build_target(callback_query_id, notifier, user, group_hash)
    if builders is None:
        return

    prefill = _group_build_prefill_for(builders)
    _answer(notifier, callback_query_id)

    if not _build_is_ready(prefill):
        _reply(
            notifier,
            chat_id,
            f'Group "{group_name}": couldn\'t confidently determine Bump Type/Object(s)/Change Type from the '
            f"{prefill['commit_count']} new commit(s) since the last build — finish this one from the web UI instead.",
        )
        return

    _reply(
        notifier,
        chat_id,
        _format_group_build_summary(group_name, builders, prefill),
        reply_markup=_group_build_confirm_keyboard(group_hash),
    )


def _handle_group_build_confirm_callback(notifier, callback_query_id, chat_id, user, group_hash):
    if not user.has_permission("builder.build"):
        _answer(notifier, callback_query_id, "You don't have permission to trigger builds.")
        return

    builders, group_name = _resolve_group_build_target(callback_query_id, notifier, user, group_hash)
    if builders is None:
        return

    # Recomputed fresh — same reasoning as _handle_build_confirm_callback.
    prefill = _group_build_prefill_for(builders)
    if not _build_is_ready(prefill):
        _answer(notifier, callback_query_id, "No longer resolvable as-is — use the web UI instead.")
        return

    object_ids = [str(obj.id) for obj in prefill["matched_objects"]]
    objects = Object.resolve(object_ids, prefill["new_object_names"])
    version = Version.query.get(builders[0].version_id)

    batch = enqueue_build_batch(
        version=version,
        bump_type=prefill["bump_type"],
        builder_branches=[(builder, builder.default_branch) for builder in builders],
        objects=objects,
        additional_description="",
        requested_by=user.id,
        change_type_id=prefill["change_type_id"],
    )

    log_activity(
        action="TRIGGER_BUILD_BATCH",
        target_type="build_batch",
        target_id=str(batch.id),
        description=(
            f"Triggered a build batch via Telegram by {user.username} "
            f"(group '{group_name}', {len(builders)} builder(s))"
        ),
        user=user,
    )
    _answer(notifier, callback_query_id, "Build queued.")
    _reply(notifier, chat_id, f'Build queued for group "{group_name}" ({len(builders)} builder(s)).')


def _handle_group_build_cancel_callback(notifier, callback_query_id, chat_id, user, group_hash):
    _answer(notifier, callback_query_id, "Cancelled.")


def _handle_callback_query(notifier, callback_query):
    callback_query_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]
    data = callback_query.get("data") or ""

    user = _resolve_user(chat_id)
    if user is None:
        _answer(notifier, callback_query_id, "Your Telegram account isn't linked.")
        return

    action, _, payload = data.partition(":")

    # Whole-group deploy/update/stop/restart: payload is a _group_hash
    # string, not a UUID — resolved to real manifests inside the shared
    # handler, not parsed here.
    if action in GROUP_ACTION_LOOKUP:
        _handle_manifest_action_callback(
            notifier, callback_query_id, chat_id, user, GROUP_ACTION_LOOKUP[action], payload, is_group=True
        )
        return

    # Whole-group build (preview/confirm/cancel): same _group_hash-not-UUID
    # shape as the deploy-style group actions above, just against Builder.
    if action in BUILD_GROUP_ACTIONS:
        if action == "gbuild":
            _handle_group_build_callback(notifier, callback_query_id, chat_id, user, payload)
        elif action == "gbconfirm":
            _handle_group_build_confirm_callback(notifier, callback_query_id, chat_id, user, payload)
        else:
            _handle_group_build_cancel_callback(notifier, callback_query_id, chat_id, user, payload)
        return

    try:
        target_id = uuid.UUID(payload)
    except ValueError:
        _answer(notifier, callback_query_id, "Invalid selection.")
        return

    if action == "run":
        _handle_run_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "status":
        _handle_status_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "review":
        _handle_review_detail_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "approve_review":
        _handle_approve_review_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "reject_review":
        _handle_reject_review_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "build":
        _handle_build_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "bconfirm":
        _handle_build_confirm_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "bcancel":
        _handle_build_cancel_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action in DEPLOY_ACTIONS:
        _handle_manifest_action_callback(notifier, callback_query_id, chat_id, user, action, target_id, is_group=False)
    else:
        _answer(notifier, callback_query_id, "Unrecognized action.")


def _handle_update(notifier, update):
    if "message" in update:
        _handle_message(notifier, update["message"])
    elif "callback_query" in update:
        _handle_callback_query(notifier, update["callback_query"])


def _tick(app):
    """Returns True if a real long-poll to Telegram happened (so the caller
    doesn't need to add its own delay before the next tick), False if this
    tick was skipped (bot commands disabled or no token configured).
    """
    global _commands_registered

    with app.app_context():
        config = get_system_config()
        if not config.telegram_bot_commands_enabled or not config.encrypted_telegram_bot_token:
            _commands_registered = False
            db.session.remove()
            return False

        bot_token = decrypt(config.encrypted_telegram_bot_token)
        if not bot_token:
            db.session.remove()
            return False

        notifier = TelegramNotifier(bot_token)

        if not _commands_registered:
            try:
                notifier.set_my_commands(BOT_COMMANDS)
                _commands_registered = True
            except Exception as exc:
                log_error(
                    source="telegram.worker.set_my_commands",
                    exc=exc,
                    description=f"Could not register the Telegram bot command menu: {exc}",
                )

        offset = (config.telegram_last_update_id or 0) + 1
        updates = notifier.get_updates(offset=offset)

        for update in updates:
            try:
                _handle_update(notifier, update)
            except Exception as exc:
                # Roll back so a broken transaction from a bad update
                # doesn't also block persisting the offset below — an
                # unhandled failure here must still advance past the
                # offending update, or it would be redelivered and retried
                # forever on every future tick.
                db.session.rollback()
                log_error(
                    source="telegram.worker.handle_update",
                    exc=exc,
                    description=f"Could not handle Telegram update {update.get('update_id')}: {exc}",
                )
            config.telegram_last_update_id = update["update_id"]
            db.session.commit()

        db.session.remove()
        return True


def _run(app):
    """Thread entry point: block until this process is the sole Telegram
    poll leader, then start polling. See _become_poll_leader for why this
    (unlike the build/deploy/workflow workers) can't just let every gunicorn
    worker process poll concurrently.
    """
    _become_poll_leader(app)
    _poll_loop(app)


def _poll_loop(app):
    while True:
        try:
            polled = _tick(app)
        except Exception as exc:
            with app.app_context():
                log_error(
                    source="telegram.worker.poll_loop",
                    exc=exc,
                    description=f"Telegram bot poll loop iteration failed: {exc}",
                )
            time.sleep(ERROR_RETRY_INTERVAL_SECONDS)
            continue

        if not polled:
            time.sleep(DISABLED_POLL_INTERVAL_SECONDS)


def start_worker(app):
    """Start this process's background Telegram-bot poll thread, once.

    Same TESTING-skip and Werkzeug-reloader guards as the build/deploy/
    workflow workers' own start_worker — see app/services/build/worker.py
    for why.
    """
    global _worker_started

    if app.config.get("TESTING"):
        return

    if is_werkzeug_reloader_parent(app):
        return

    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    thread = threading.Thread(target=_run, args=(app,), daemon=True, name="telegram-bot")
    thread.start()
