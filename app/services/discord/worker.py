"""Discord bot command handling — connects via the Gateway WebSocket
(app/services/discord/gateway.py) and lets a linked User
(User.discord_user_id) run/check Workflows and trigger manual Builds/
Deployment Manifest actions straight from Discord's native "/" slash-command
picker, the same permission codes and accessibility rules
(Workflow.is_accessible_to etc.) the web UI and the Telegram integration
(app/services/telegram/worker.py) already enforce.

Structurally mirrors telegram/worker.py's dispatch shape wherever Discord's
protocol allows: the same DEPLOY_ACTIONS-table-driven manifest actions and
the same three-tier action:payload dispatch for buttons/selects (both
imported from app.services.bot_shared, shared with Telegram so neither
provider duplicates the group-resolution logic), the same log_activity
calls (just "via Discord" instead of "via Telegram"). Differs where
Discord's protocol genuinely differs from Telegram's: no long-poll — a
persistent Gateway connection instead (see gateway.py and _supervise
below), slash commands + Buttons/Select Menus instead of a bot command menu
+ inline keyboard (Discord's 5-ActionRow-per-message limit means a "pick
one of N" list uses a String Select, not one button per row), and a
3-second interaction-ack deadline that forces the four AI-backed
build-prefill interactions (build/bconfirm/gbuild/gbconfirm) onto a
deferred-response + background-thread path — see _build_executor below and
that module's own docstring for why a slow AI call can never run inline on
the Gateway's own read thread.
"""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.extensions import db
from app.models import Builder, ChangeType, DeploymentManifest, Object, User, Version, Workflow, WorkflowRun, WorkflowStepRun
from app.services.bot_shared import (
    BUILD_GROUP_ACTIONS,
    DEPLOY_ACTIONS,
    GROUP_ACTION_LOOKUP,
    GROUP_ACTION_PREFIX,
    _accessible_with_servers,
    _build_is_ready,
    _build_prefill_for,
    _currently_live,
    _group_build_prefill_for,
    _group_hash,
    _groups_among,
    _manifests_in_group,
    _single_version_groups_among,
    _target_manifests,
    format_review_summary,
    resolve_group_build_target,
)
from app.services.build.worker import enqueue_build_batch
from app.services.deployment.worker import enqueue_deployment_run
from app.services.discord.client import DiscordClient
from app.services.discord.gateway import DiscordGateway
from app.services.discord.helpers import review_components, status_components
from app.services.workflow.worker import approve_awaiting_step, enqueue_workflow_run, reject_awaiting_step
from app.utils.crypto import decrypt
from app.utils.error_logger import log_error
from app.utils.logger import log_activity
from app.utils.runtime import is_werkzeug_reloader_parent
from app.utils.system_config import get_system_config

DISABLED_POLL_INTERVAL_SECONDS = 5

RUN_LIST_LIMIT = 10
STATUS_LIST_LIMIT = 5
REVIEW_LIST_LIMIT = 10
BUILD_LIST_LIMIT = 10
MANIFEST_LIST_LIMIT = 10
GROUP_LIST_LIMIT = 10

# Discord interaction/component/response-type ints — see
# https://discord.com/developers/docs/interactions/receiving-and-responding
APPLICATION_COMMAND_TYPE = 2
MESSAGE_COMPONENT_TYPE = 3

CHANNEL_MESSAGE_WITH_SOURCE = 4
DEFERRED_UPDATE_MESSAGE = 6
UPDATE_MESSAGE = 7

ACTION_ROW = 1
BUTTON = 2
STRING_SELECT = 3
BUTTON_STYLE_SUCCESS = 3
BUTTON_STYLE_DANGER = 4

EPHEMERAL_FLAG = 1 << 6

COMMANDS = [
    {"name": "help", "description": "Show help", "type": 1},
    {"name": "run", "description": "Run a workflow", "type": 1},
    {"name": "status", "description": "Check a workflow run's status", "type": 1},
    {"name": "review", "description": "Review a build awaiting approval", "type": 1},
    {"name": "build", "description": "Build an image directly", "type": 1},
    {"name": "deploy", "description": "Deploy a manifest directly", "type": 1},
    {"name": "update", "description": "Update a live manifest directly", "type": 1},
    {"name": "stop", "description": "Stop a live manifest", "type": 1},
    {"name": "restart", "description": "Restart a live manifest", "type": 1},
]

HELP_TEXT = (
    "Hey, I'm the MASIMPLE CICD bot! Here's what I can do:\n"
    "`/run` — pick a workflow and I'll kick it off\n"
    "`/status` — see how your recent runs are doing\n"
    "`/review` — approve or reject a build that's waiting on you\n"
    "`/build` — build an image directly, no workflow needed\n"
    "`/deploy` — deploy a manifest directly\n"
    "`/update` — update a manifest that's already live\n"
    "`/stop` — stop a live manifest\n"
    "`/restart` — restart a live manifest's workload(s)\n\n"
    "Tip: you can DM me these commands directly, no need to use a server channel."
)

_worker_started = False
_worker_lock = threading.Lock()

# Arbitrary but stable, and distinct from Telegram's own
# TELEGRAM_POLL_LEADER_LOCK_KEY (917_442_101) — see
# app/services/telegram/worker.py's _become_poll_leader for the full
# rationale (only one gunicorn worker process may hold the live connection).
DISCORD_POLL_LEADER_LOCK_KEY = 917_442_102
_leader_lock_connection = None

# Bounded pool for the 4 build-prefill interactions only (build/bconfirm/
# gbuild/gbconfirm) — see this module's own docstring for why these can't
# run inline on the Gateway's read thread. Bounded rather than a raw
# threading.Thread per interaction, so a burst of build taps can't grow
# threads unboundedly.
_build_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="discord-build")


def _become_poll_leader(app):
    """Same Postgres advisory-lock pattern as Telegram's own
    _become_poll_leader — only one gunicorn worker process should hold the
    live Gateway connection, same reasoning as Telegram's getUpdates
    (a second concurrent connection with the same token conflicts).
    """
    global _leader_lock_connection

    with app.app_context():
        raw_conn = db.engine.raw_connection()
        raw_conn.detach()
        cursor = raw_conn.cursor()
        cursor.execute("SELECT pg_advisory_lock(%s)", (DISCORD_POLL_LEADER_LOCK_KEY,))
        cursor.close()
        raw_conn.commit()

    _leader_lock_connection = raw_conn


def _try(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        log_error(source="discord.worker.respond", exc=exc, description=f"Could not send a Discord interaction response: {exc}")


def _ack_message(client, interaction, content, components=None):
    """Immediate type:4 response — a NEW ephemeral message, used for a
    top-level slash-command invocation (nothing existing to edit yet).
    """
    data = {"content": content, "flags": EPHEMERAL_FLAG}
    if components is not None:
        data["components"] = components
    _try(client.respond_to_interaction, interaction["id"], interaction["token"], CHANNEL_MESSAGE_WITH_SOURCE, data)


def _ack_update(client, interaction, content, components=None):
    """Immediate type:7 response — edits the message the tapped
    button/select lives on, mirroring Telegram's _answer-then-_reply
    single-tap UX in one round trip.
    """
    data = {"content": content}
    if components is not None:
        data["components"] = components
    _try(client.respond_to_interaction, interaction["id"], interaction["token"], UPDATE_MESSAGE, data)


def _ack_deferred_update(client, interaction):
    _try(client.respond_to_interaction, interaction["id"], interaction["token"], DEFERRED_UPDATE_MESSAGE)


def _followup(client, application_id, interaction, content, components=None):
    _try(client.edit_original_response, application_id, interaction["token"], content, components=components)


def _option(label, value):
    # Discord caps both fields at 100 chars — truncate defensively rather
    # than letting an unusually long name/hash 400 the whole response.
    return {"label": label[:100], "value": value[:100]}


def _select_component(custom_id, placeholder, options):
    return [{"type": ACTION_ROW, "components": [{"type": STRING_SELECT, "custom_id": custom_id, "placeholder": placeholder, "options": options}]}]


def _confirm_cancel_components(confirm_id, cancel_id):
    return [
        {
            "type": ACTION_ROW,
            "components": [
                {
                    "type": BUTTON,
                    "style": BUTTON_STYLE_SUCCESS,
                    "label": "Confirm build",
                    "custom_id": confirm_id,
                },
                {
                    "type": BUTTON,
                    "style": BUTTON_STYLE_DANGER,
                    "label": "Cancel",
                    "custom_id": cancel_id,
                },
            ],
        }
    ]


def _workflow_options(workflows):
    return [_option(w.name, f"run:{w.id}") for w in workflows]


def _run_status_options(runs):
    return [_option(f"{r.workflow.name} ({r.status})", f"status:{r.id}") for r in runs]


def _review_options(step_runs):
    return [_option(f"{sr.run.workflow.name} (step {sr.step_order})", f"review:{sr.id}") for sr in step_runs]


def _manifest_options(base_action, manifests, groups):
    options = [_option(m.name, f"{base_action}:{m.id}") for m in manifests]
    group_prefix = GROUP_ACTION_PREFIX[base_action]
    for name in sorted(groups)[:GROUP_LIST_LIMIT]:
        options.append(_option(f"Whole group: {name}", f"{group_prefix}:{_group_hash(name)}"))
    return options


def _builder_options(builders, groups):
    options = [_option(b.name, f"build:{b.id}") for b in builders]
    for name in sorted(groups)[:GROUP_LIST_LIMIT]:
        options.append(_option(f"Whole group: {name}", f"gbuild:{_group_hash(name)}"))
    return options


# ---- slash-command handlers (mirror telegram/worker.py's _handle_*_command) ----


def _handle_help_command(client, interaction, user):
    _ack_message(client, interaction, HELP_TEXT)


def _handle_run_command(client, interaction, user):
    if not user.has_permission("workflow.run"):
        _ack_message(client, interaction, "You don't have permission to run workflows — ask an admin if you think that's wrong.")
        return

    workflows = [w for w in Workflow.query.filter_by(is_active=True).order_by(Workflow.name).all() if w.is_accessible_to(user)][:RUN_LIST_LIMIT]
    if not workflows:
        _ack_message(client, interaction, "No workflows available for you to run right now.")
        return

    _ack_message(client, interaction, "Which workflow would you like to run?", components=_select_component("run_select", "Pick a workflow", _workflow_options(workflows)))


def _handle_status_command(client, interaction, user):
    runs = WorkflowRun.query.filter_by(triggered_by=user.id).order_by(WorkflowRun.created_at.desc()).limit(STATUS_LIST_LIMIT).all()
    if not runs:
        _ack_message(client, interaction, "You haven't triggered any workflow runs yet.")
        return

    _ack_message(client, interaction, "Which run would you like to check?", components=_select_component("status_select", "Pick a run", _run_status_options(runs)))


def _handle_review_command(client, interaction, user):
    # Same authorization as the web review panel and Telegram's /review:
    # any workflow.run holder who can see the workflow, not just whoever
    # triggered the run — a shared review queue.
    if not user.has_permission("workflow.run"):
        _ack_message(client, interaction, "You don't have permission to review workflow builds — ask an admin if you think that's wrong.")
        return

    step_runs = [
        sr for sr in WorkflowStepRun.query.filter_by(status="awaiting_review").order_by(WorkflowStepRun.created_at.desc()).all()
        if sr.run.workflow.is_accessible_to(user)
    ][:REVIEW_LIST_LIMIT]
    if not step_runs:
        _ack_message(client, interaction, "Nothing's waiting on a review right now.")
        return

    _ack_message(client, interaction, "Which build would you like to review?", components=_select_component("review_select", "Pick a build", _review_options(step_runs)))


def _handle_build_command(client, interaction, user):
    if not user.has_permission("builder.build"):
        _ack_message(client, interaction, "You don't have permission to trigger builds — ask an admin if you think that's wrong.")
        return

    builders = [
        b for b in Builder.query.filter_by(is_active=True).order_by(Builder.name).all() if b.default_branch and b.is_accessible_to(user)
    ][:BUILD_LIST_LIMIT]
    if not builders:
        _ack_message(client, interaction, "No builders available for you to build right now.")
        return

    groups = _single_version_groups_among(builders)
    _ack_message(
        client, interaction, "Which builder would you like to build (or pick a whole group)?",
        components=_select_component("build_select", "Pick a builder", _builder_options(builders, groups)),
    )


def _handle_manifest_list_command(client, interaction, user, base_action):
    spec = DEPLOY_ACTIONS[base_action]
    if not user.has_permission(spec["permission"]):
        _ack_message(client, interaction, f"You don't have permission to {spec['verb']} manifests — ask an admin if you think that's wrong.")
        return

    manifests = _accessible_with_servers(_target_manifests(active_only=not spec["teardown"]), user)
    if spec["teardown"]:
        manifests = _currently_live(manifests)

    if not manifests:
        _ack_message(client, interaction, f"No manifests available for you to {spec['verb']} right now.")
        return

    manifests = manifests[:MANIFEST_LIST_LIMIT]
    groups = _groups_among(manifests)
    _ack_message(
        client, interaction, f"Which manifest would you like to {spec['verb']} (or pick a whole group)?",
        components=_select_component(f"{base_action}_select", "Pick a manifest", _manifest_options(base_action, manifests, groups)),
    )


COMMAND_HANDLERS = {
    "help": _handle_help_command,
    "run": _handle_run_command,
    "status": _handle_status_command,
    "review": _handle_review_command,
    "build": _handle_build_command,
    "deploy": lambda client, interaction, user: _handle_manifest_list_command(client, interaction, user, "deploy"),
    "update": lambda client, interaction, user: _handle_manifest_list_command(client, interaction, user, "update"),
    "stop": lambda client, interaction, user: _handle_manifest_list_command(client, interaction, user, "stop"),
    "restart": lambda client, interaction, user: _handle_manifest_list_command(client, interaction, user, "restart"),
}


# ---- component handlers (mirror telegram/worker.py's _handle_*_callback) ----


def _handle_run_select(client, interaction, user, workflow_id):
    if not user.has_permission("workflow.run"):
        _ack_update(client, interaction, "You don't have permission to run workflows.")
        return

    workflow = Workflow.query.get(workflow_id)
    if workflow is None or not workflow.is_active or not workflow.is_accessible_to(user):
        _ack_update(client, interaction, "That workflow isn't available anymore.")
        return

    run = enqueue_workflow_run(workflow, triggered_by=user.id)
    log_activity(
        action="RUN_WORKFLOW", target_type="workflow_run", target_id=str(run.id),
        description=f"Workflow '{workflow.name}' run via Discord by {user.username}", user=user,
    )
    # A real Check Status button instead of a "type /status" instruction —
    # tapping it reuses _handle_status_select directly, same status:<uuid>
    # action token /status's own select already dispatches through.
    _ack_update(
        client, interaction, f'Queued a run of **{workflow.name}**! I\'ll let you know how it goes.',
        components=status_components(run.id),
    )


def _format_run_status(run):
    lines = [f'**{run.workflow.name}** — {run.status}']
    for step_run in run.step_runs.all():
        line = f"  Step {step_run.step_order} ({step_run.step_type}): {step_run.status}"
        if step_run.error:
            line += f" — {step_run.error}"
        lines.append(line)
    return "\n".join(lines)


def _handle_status_select(client, interaction, user, run_id):
    # Deliberately narrower than Workflow.is_accessible_to: only a run this
    # user personally triggered, same scoping as Telegram's /status.
    run = WorkflowRun.query.get(run_id)
    if run is None or run.triggered_by != user.id:
        _ack_update(client, interaction, "Couldn't find that run — it may not be yours to check.")
        return

    _ack_update(client, interaction, _format_run_status(run))


def _awaiting_review_step_run_for_user(step_run_id, user):
    step_run = WorkflowStepRun.query.get(step_run_id)
    if step_run is None or step_run.status != "awaiting_review":
        return None
    if not step_run.run.workflow.is_accessible_to(user):
        return None
    return step_run


def _handle_review_select(client, interaction, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _ack_update(client, interaction, "You don't have permission to review workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _ack_update(client, interaction, "This one's already been taken care of — nothing left to review here.")
        return

    _ack_update(client, interaction, format_review_summary(step_run), components=review_components(step_run))


def _handle_approve_review(client, interaction, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _ack_update(client, interaction, "You don't have permission to approve workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _ack_update(client, interaction, "This one's already been taken care of — nothing left to review here.")
        return

    # Discord has no form to collect a missing Version Bump/Change Type the
    # way the web review panel's dropdowns do — approve as-is only when the
    # AI/heuristic suggestion already has both, same rule as Telegram.
    if not step_run.suggested_bump_type or not step_run.suggested_change_type_id:
        _ack_update(client, interaction, "I'm missing a Version Bump or Change Type for this one — please approve it from the web UI instead.")
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
        _ack_update(client, interaction, str(exc))
        return

    log_activity(
        action="APPROVE_WORKFLOW_BUILD_STEP", target_type="workflow_run", target_id=str(run.id),
        description=f"Approved AI-suggested build metadata for a step in workflow '{run.workflow.name}' via Discord by {user.username}",
        user=user,
    )
    _ack_update(client, interaction, f'Approved! The build for **{run.workflow.name}** is queued.')


def _handle_reject_review(client, interaction, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _ack_update(client, interaction, "You don't have permission to reject workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _ack_update(client, interaction, "This one's already been taken care of — nothing left to review here.")
        return

    run = step_run.run
    reject_awaiting_step(step_run)
    log_activity(
        action="REJECT_WORKFLOW_BUILD_STEP", target_type="workflow_run", target_id=str(run.id),
        description=f"Rejected AI-suggested build metadata for a step in workflow '{run.workflow.name}' via Discord by {user.username}",
        user=user,
    )
    _ack_update(client, interaction, f'Got it — rejected the build step for **{run.workflow.name}**.')


def _handle_manifest_action_select(client, interaction, user, base_action, payload, is_group):
    """Shared body for the deploy/update/stop/restart selections, both the
    single-manifest (`payload` a manifest UUID) and whole-group (`payload` a
    _group_hash) shapes — mirrors telegram/worker.py's
    _handle_manifest_action_callback exactly, re-running the same server-
    side checks deployment_manifests.routes makes (a stale button must
    still be caught here, not just at list-build time).
    """
    spec = DEPLOY_ACTIONS[base_action]
    if not user.has_permission(spec["permission"]):
        _ack_update(client, interaction, f"You don't have permission to {spec['verb']} manifests.")
        return

    if is_group:
        manifests, group_name = _manifests_in_group(payload, active_only=not spec["teardown"])
        if not manifests:
            _ack_update(client, interaction, "That group isn't available anymore.")
            return
        manifests.sort(key=lambda m: (m.order, m.name), reverse=spec["teardown"])
    else:
        manifest = DeploymentManifest.query.get(payload)
        if manifest is None:
            _ack_update(client, interaction, "That manifest isn't available anymore.")
            return
        manifests = [manifest]
        group_name = None

    if not spec["teardown"]:
        disabled = [m.name for m in manifests if not m.is_active]
        if disabled:
            _ack_update(client, interaction, f"Can't {spec['verb']} — disabled manifest(s): {', '.join(disabled)}.")
            return

    if any(not m.is_accessible_to(user) for m in manifests):
        _ack_update(client, interaction, "You don't have access to one or more of these manifests.")
        return

    servers = {server for m in manifests for server in m.target_servers}
    if any(not server.is_accessible_to(user) for server in servers):
        _ack_update(client, interaction, "You don't have access to one or more target servers.")
        return
    if not spec["teardown"] and not servers:
        _ack_update(client, interaction, "No target servers are configured for this selection.")
        return

    run, execution_count = enqueue_deployment_run(
        manifests=manifests, triggered_by=user.id, group_name=group_name,
        action=base_action if spec["teardown"] else "deploy",
    )

    if spec["teardown"] and execution_count == 0:
        db.session.delete(run)
        db.session.commit()
        _ack_update(client, interaction, "Nothing's currently deployed for this selection — nothing to do.")
        return

    log_activity(
        action=spec["activity"], target_type="deployment_run", target_id=str(run.id),
        description=(
            f"Triggered a {spec['verb']} run via Discord by {user.username} "
            f"({len(manifests)} manifest(s), {execution_count} execution(s))"
        ),
        user=user,
    )
    label = group_name or manifests[0].name
    _ack_update(
        client, interaction,
        f'{spec["verb"].capitalize()} queued for **{label}** ({execution_count} execution(s)) — I\'ll let you know how it goes.',
    )


# ---- deferred build handlers (AI-backed prefill — see module docstring) ----


def _format_build_summary(builder, prefill):
    change_type = ChangeType.query.get(prefill["change_type_id"]) if prefill["change_type_id"] else None
    objects = [obj.name for obj in prefill["matched_objects"]] + list(prefill["new_object_names"])
    return (
        f'**{builder.name}** — {prefill["commit_count"]} new commit(s) since the last build. Here\'s what I found:\n'
        f"Bump Type: {prefill['bump_type']}\n"
        f"Object(s): {', '.join(objects) or '(none)'}\n"
        f"Change Type: {change_type.name if change_type else '(none)'}\n"
        f"Description: {prefill['description'] or '(none)'}\n\n"
        f"Look good?"
    )


def _format_group_build_summary(group_name, builders, prefill):
    change_type = ChangeType.query.get(prefill["change_type_id"]) if prefill["change_type_id"] else None
    objects = [obj.name for obj in prefill["matched_objects"]] + list(prefill["new_object_names"])
    builder_names = ", ".join(sorted(builder.name for builder in builders))
    return (
        f'Group **{group_name}** ({builder_names}) — {prefill["commit_count"]} new commit(s) since the last build. '
        f"Here's what I found:\n"
        f"Bump Type: {prefill['bump_type']}\n"
        f"Object(s): {', '.join(objects) or '(none)'}\n"
        f"Change Type: {change_type.name if change_type else '(none)'}\n"
        f"Description: {prefill['description'] or '(none)'}\n\n"
        f"Look good?"
    )


def _builder_still_buildable(builder, user):
    return builder is not None and builder.is_active and builder.default_branch and builder.is_accessible_to(user)


def _handle_build_select(app, client, interaction, user, builder_id):
    if not user.has_permission("builder.build"):
        _ack_update(client, interaction, "You don't have permission to trigger builds.")
        return

    builder = Builder.query.get(builder_id)
    if not _builder_still_buildable(builder, user):
        _ack_update(client, interaction, "That builder isn't available anymore.")
        return

    _ack_deferred_update(client, interaction)
    _build_executor.submit(_run_build_preview, app, client, interaction, builder.id)


def _run_build_preview(app, client, interaction, builder_id):
    with app.app_context():
        try:
            application_id = get_system_config().discord_application_id
            builder = Builder.query.get(builder_id)
            prefill = _build_prefill_for(builder)
            if not _build_is_ready(prefill):
                _followup(
                    client, application_id, interaction,
                    f'**{builder.name}**: I couldn\'t confidently work out the Bump Type/Object(s)/Change Type from the '
                    f"{prefill['commit_count']} new commit(s) since the last build — could you finish this one from the web UI instead?",
                )
                return
            _followup(
                client, application_id, interaction, _format_build_summary(builder, prefill),
                components=_confirm_cancel_components(f"bconfirm:{builder.id}", f"bcancel:{builder.id}"),
            )
        except Exception as exc:
            log_error(source="discord.worker.run_build_preview", exc=exc, description=f"Could not compute a Discord build preview: {exc}")
        finally:
            db.session.remove()


def _handle_build_confirm_select(app, client, interaction, user, builder_id):
    if not user.has_permission("builder.build"):
        _ack_update(client, interaction, "You don't have permission to trigger builds.")
        return

    builder = Builder.query.get(builder_id)
    if not _builder_still_buildable(builder, user):
        _ack_update(client, interaction, "That builder isn't available anymore.")
        return

    _ack_deferred_update(client, interaction)
    _build_executor.submit(_run_build_confirm, app, client, interaction, builder.id, user.id)


def _run_build_confirm(app, client, interaction, builder_id, user_id):
    with app.app_context():
        try:
            application_id = get_system_config().discord_application_id
            user = User.query.get(user_id)
            builder = Builder.query.get(builder_id)
            if not _builder_still_buildable(builder, user):
                _followup(client, application_id, interaction, "That builder isn't available anymore.")
                return

            # Recomputed fresh rather than trusting the earlier preview's
            # values — same reasoning as Telegram's confirm step: the
            # component's custom_id can't carry the full suggestion, and
            # this is deterministic enough over the few-second preview-to-
            # confirm gap not to persist a stand-in row.
            prefill = _build_prefill_for(builder)
            if not _build_is_ready(prefill):
                _followup(client, application_id, interaction, "That's no longer resolvable as-is — please use the web UI instead.")
                return

            object_ids = [str(obj.id) for obj in prefill["matched_objects"]]
            objects = Object.resolve(object_ids, prefill["new_object_names"])
            batch = enqueue_build_batch(
                version=builder.version, bump_type=prefill["bump_type"],
                builder_branches=[(builder, builder.default_branch)], objects=objects,
                additional_description="", requested_by=user.id, change_type_id=prefill["change_type_id"],
            )
            log_activity(
                action="TRIGGER_BUILD_BATCH", target_type="build_batch", target_id=str(batch.id),
                description=f"Triggered a build batch via Discord by {user.username} (builder '{builder.name}')", user=user,
            )
            _followup(client, application_id, interaction, f'Build queued for **{builder.name}** — I\'ll let you know when it\'s done.')
        except Exception as exc:
            log_error(source="discord.worker.run_build_confirm", exc=exc, description=f"Could not confirm a Discord build: {exc}")
        finally:
            db.session.remove()


def _handle_build_cancel_select(client, interaction, user, builder_id):
    _ack_update(client, interaction, "No worries, cancelled.")


def _handle_group_build_select(app, client, interaction, user, group_hash):
    if not user.has_permission("builder.build"):
        _ack_update(client, interaction, "You don't have permission to trigger builds.")
        return

    _ack_deferred_update(client, interaction)
    _build_executor.submit(_run_group_build_preview, app, client, interaction, group_hash, user.id)


def _run_group_build_preview(app, client, interaction, group_hash, user_id):
    with app.app_context():
        try:
            application_id = get_system_config().discord_application_id
            user = User.query.get(user_id)
            builders, group_name, error = resolve_group_build_target(user, group_hash)
            if error:
                _followup(client, application_id, interaction, error)
                return

            prefill = _group_build_prefill_for(builders)
            if not _build_is_ready(prefill):
                _followup(
                    client, application_id, interaction,
                    f'Group **{group_name}**: I couldn\'t confidently work out the Bump Type/Object(s)/Change Type from the '
                    f"{prefill['commit_count']} new commit(s) since the last build — could you finish this one from the web UI instead?",
                )
                return
            _followup(
                client, application_id, interaction, _format_group_build_summary(group_name, builders, prefill),
                components=_confirm_cancel_components(f"gbconfirm:{group_hash}", f"gbcancel:{group_hash}"),
            )
        except Exception as exc:
            log_error(source="discord.worker.run_group_build_preview", exc=exc, description=f"Could not compute a Discord group build preview: {exc}")
        finally:
            db.session.remove()


def _handle_group_build_confirm_select(app, client, interaction, user, group_hash):
    if not user.has_permission("builder.build"):
        _ack_update(client, interaction, "You don't have permission to trigger builds.")
        return

    _ack_deferred_update(client, interaction)
    _build_executor.submit(_run_group_build_confirm, app, client, interaction, group_hash, user.id)


def _run_group_build_confirm(app, client, interaction, group_hash, user_id):
    with app.app_context():
        try:
            application_id = get_system_config().discord_application_id
            user = User.query.get(user_id)
            builders, group_name, error = resolve_group_build_target(user, group_hash)
            if error:
                _followup(client, application_id, interaction, error)
                return

            prefill = _group_build_prefill_for(builders)
            if not _build_is_ready(prefill):
                _followup(client, application_id, interaction, "That's no longer resolvable as-is — please use the web UI instead.")
                return

            object_ids = [str(obj.id) for obj in prefill["matched_objects"]]
            objects = Object.resolve(object_ids, prefill["new_object_names"])
            version = Version.query.get(builders[0].version_id)
            batch = enqueue_build_batch(
                version=version, bump_type=prefill["bump_type"],
                builder_branches=[(builder, builder.default_branch) for builder in builders],
                objects=objects, additional_description="", requested_by=user.id,
                change_type_id=prefill["change_type_id"],
            )
            log_activity(
                action="TRIGGER_BUILD_BATCH", target_type="build_batch", target_id=str(batch.id),
                description=(
                    f"Triggered a build batch via Discord by {user.username} "
                    f"(group '{group_name}', {len(builders)} builder(s))"
                ),
                user=user,
            )
            _followup(
                client, application_id, interaction,
                f'Build queued for group **{group_name}** ({len(builders)} builder(s)) — I\'ll let you know when it\'s done.',
            )
        except Exception as exc:
            log_error(source="discord.worker.run_group_build_confirm", exc=exc, description=f"Could not confirm a Discord group build: {exc}")
        finally:
            db.session.remove()


def _handle_group_build_cancel_select(client, interaction, user, group_hash):
    _ack_update(client, interaction, "No worries, cancelled.")


# ---- top-level dispatch ----


def _interaction_user_id(interaction):
    member = interaction.get("member")
    if member is not None:
        return (member.get("user") or {}).get("id")
    return (interaction.get("user") or {}).get("id")


def _resolve_user(discord_user_id):
    return User.query.filter_by(discord_user_id=str(discord_user_id)).first()


def _component_action_payload(interaction):
    """A Button's custom_id IS the action:payload string directly; a Select
    Menu's custom_id only identifies the select itself — the chosen
    option's own value (interaction["data"]["values"][0]) carries the
    action:payload, mirroring where Telegram's callback_data lives on an
    inline-keyboard button either way.
    """
    data = interaction["data"]
    if "values" in data:
        return data["values"][0]
    return data["custom_id"]


def _dispatch_command(app, client, interaction, user):
    name = interaction["data"]["name"]
    handler = COMMAND_HANDLERS.get(name)
    if handler is None:
        _ack_message(client, interaction, "Unrecognized command — try /help to see what I can do.")
        return
    handler(client, interaction, user)


def _dispatch_component(app, client, interaction, user):
    action_payload = _component_action_payload(interaction)
    action, _, payload = action_payload.partition(":")

    # Whole-group deploy/update/stop/restart: payload is a _group_hash
    # string, not a UUID.
    if action in GROUP_ACTION_LOOKUP:
        _handle_manifest_action_select(client, interaction, user, GROUP_ACTION_LOOKUP[action], payload, is_group=True)
        return

    # Whole-group build (preview/confirm/cancel): same _group_hash-not-UUID
    # shape as the deploy-style group actions above, just against Builder.
    if action in BUILD_GROUP_ACTIONS:
        if action == "gbuild":
            _handle_group_build_select(app, client, interaction, user, payload)
        elif action == "gbconfirm":
            _handle_group_build_confirm_select(app, client, interaction, user, payload)
        else:
            _handle_group_build_cancel_select(client, interaction, user, payload)
        return

    try:
        target_id = uuid.UUID(payload)
    except ValueError:
        _ack_update(client, interaction, "Invalid selection — please try again.")
        return

    if action == "run":
        _handle_run_select(client, interaction, user, target_id)
    elif action == "status":
        _handle_status_select(client, interaction, user, target_id)
    elif action == "review":
        _handle_review_select(client, interaction, user, target_id)
    elif action == "approve_review":
        _handle_approve_review(client, interaction, user, target_id)
    elif action == "reject_review":
        _handle_reject_review(client, interaction, user, target_id)
    elif action == "build":
        _handle_build_select(app, client, interaction, user, target_id)
    elif action == "bconfirm":
        _handle_build_confirm_select(app, client, interaction, user, target_id)
    elif action == "bcancel":
        _handle_build_cancel_select(client, interaction, user, target_id)
    elif action in DEPLOY_ACTIONS:
        _handle_manifest_action_select(client, interaction, user, action, target_id, is_group=False)
    else:
        _ack_update(client, interaction, "Unrecognized action.")


def _handle_interaction(app, client, interaction):
    discord_user_id = _interaction_user_id(interaction)
    user = _resolve_user(discord_user_id) if discord_user_id else None
    interaction_type = interaction.get("type")

    if user is None:
        if interaction_type in (APPLICATION_COMMAND_TYPE, MESSAGE_COMPONENT_TYPE):
            _ack_message(
                client, interaction,
                "Looks like your Discord account isn't linked yet. Ask an admin to set your Discord User ID in Account Settings, then try again!",
            )
        return

    if interaction_type == APPLICATION_COMMAND_TYPE:
        _dispatch_command(app, client, interaction, user)
    elif interaction_type == MESSAGE_COMPONENT_TYPE:
        _dispatch_component(app, client, interaction, user)


def _register_commands(client):
    config = get_system_config()
    application_id = config.discord_application_id
    if not application_id:
        log_error(
            source="discord.worker.register_commands", exc=None,
            description="Cannot register Discord slash commands: no Application ID configured (see /config).",
        )
        return
    client.bulk_register_commands(application_id, COMMANDS)


def _on_dispatch(app, client, event_type, data):
    with app.app_context():
        try:
            if event_type == "READY":
                _register_commands(client)
            elif event_type == "INTERACTION_CREATE":
                _handle_interaction(app, client, data)
        except Exception as exc:
            # Roll back so a broken transaction from a bad interaction
            # doesn't linger on this thread's session.
            db.session.rollback()
            log_error(
                source="discord.worker.on_dispatch", exc=exc,
                description=f"Could not handle Discord dispatch event '{event_type}': {exc}",
            )
        finally:
            db.session.remove()


def _run(app):
    """Thread entry point: block until this process is the sole Discord
    Gateway leader, then supervise the live connection.
    """
    _become_poll_leader(app)
    _supervise(app)


def _supervise(app):
    """Unlike Telegram's _poll_loop (one blocking HTTP round-trip per
    iteration), Discord's Gateway is a persistent connection — this loop's
    job is to start/stop a background `discord-gateway` thread as
    discord_bot_commands_enabled/the bot token change, checking every
    DISABLED_POLL_INTERVAL_SECONDS, and to actually tear down a live
    connection the moment the config is turned off rather than merely
    skipping a poll iteration.
    """
    active_gateway = None
    active_token = None
    gateway_thread = None

    while True:
        try:
            with app.app_context():
                config = get_system_config()
                enabled = bool(config.discord_bot_commands_enabled and config.encrypted_discord_bot_token)
                bot_token = decrypt(config.encrypted_discord_bot_token) if enabled else None

            if not enabled or not bot_token:
                if active_gateway is not None:
                    active_gateway.stop()
                    gateway_thread.join(timeout=10)
                    active_gateway = None
                    active_token = None
                    gateway_thread = None
                time.sleep(DISABLED_POLL_INTERVAL_SECONDS)
                continue

            if active_gateway is not None and bot_token != active_token:
                active_gateway.stop()
                gateway_thread.join(timeout=10)
                active_gateway = None
                gateway_thread = None

            if active_gateway is None or gateway_thread is None or not gateway_thread.is_alive():
                client = DiscordClient(bot_token)
                gateway = DiscordGateway(bot_token, client, on_dispatch=lambda event_type, data, _c=client: _on_dispatch(app, _c, event_type, data))
                active_gateway = gateway
                active_token = bot_token
                gateway_thread = threading.Thread(target=gateway.run, daemon=True, name="discord-gateway")
                gateway_thread.start()

        except Exception as exc:
            with app.app_context():
                log_error(
                    source="discord.worker.supervise", exc=exc,
                    description=f"Discord bot supervisor loop iteration failed: {exc}",
                )

        time.sleep(DISABLED_POLL_INTERVAL_SECONDS)


def start_worker(app):
    """Start this process's background Discord-bot thread, once. Same
    TESTING-skip and Werkzeug-reloader guards as the other background
    workers — see app/services/build/worker.py for why.
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

    thread = threading.Thread(target=_run, args=(app,), daemon=True, name="discord-bot")
    thread.start()
