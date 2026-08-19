from app.models import User, WorkflowStepRun
from app.services.telegram.client import TelegramNotifier
from app.utils.crypto import decrypt
from app.utils.error_logger import log_error
from app.utils.system_config import get_system_config


def notify_user(user, text, reply_markup=None):
    """Best-effort Telegram message to this specific user's own chat —
    used for the forgot-password flow (request + success), since only the
    account owner can act on those, and for the Workflow run/review
    notifications below. For wrong-password/lockout/login alerts about a
    user's activity, see notify_security_contact() below — those go to the
    designated security contact, not to the affected user.

    `reply_markup`, when given, attaches an inline keyboard (e.g. the
    Approve/Reject buttons on a review notification — see
    notify_awaiting_review below).

    Mirrors this app's other best-effort integrations (e.g.
    worker._record_commit_history): a Telegram failure, missing bot token,
    or a user with no chat ID configured must never fail the login/reset
    flow calling this, so every failure path here just logs and returns
    rather than raising. Returns True only if a message was actually sent.
    """
    if not user or not user.telegram_chat_id:
        return False

    config = get_system_config()
    if not config.telegram_notifications_enabled:
        return False

    try:
        bot_token = decrypt(config.encrypted_telegram_bot_token)
        if not bot_token:
            return False
        TelegramNotifier(bot_token).send_message(user.telegram_chat_id, text, reply_markup=reply_markup)
        return True
    except Exception as exc:
        log_error(
            source="telegram.notify_user",
            exc=exc,
            description=f"Could not send Telegram notification to user '{user.username}': {exc}",
        )
        return False


def notify_run_finished(run):
    """Best-effort Telegram push to whoever triggered a WorkflowRun (via
    Telegram or the web UI alike) once it reaches a terminal status — see
    app/services/workflow/worker.py.finish_step(), the one place both a
    failed-and-stopped run and a fully-completed run are decided. Same
    "never fails the caller" contract as notify_user(), which this
    delegates to once the triggering user is resolved.
    """
    if not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    status_label = {
        "success": "succeeded",
        "failed": "failed",
        "completed_with_failures": "completed with failures",
    }.get(run.status, run.status)
    text = f"Workflow \"{run.workflow.name}\" {status_label}."
    return notify_user(user, text)


def format_review_summary(step_run):
    """Plain-text rendering of an awaiting_review WorkflowStepRun's
    AI-suggested build values — shared by the push notification below and
    Telegram's /review command detail view
    (app/services/telegram/worker.py), so both show the same thing the web
    review panel does (see workflows/run.html's review form, pre-filled
    from these same suggested_* columns).
    """
    change_type_name = step_run.suggested_change_type.name if step_run.suggested_change_type else None
    return (
        f'Workflow "{step_run.run.workflow.name}" needs review before building:\n'
        f"Version Bump: {step_run.suggested_bump_type or '(not suggested — set on the web)'}\n"
        f"Change Type: {change_type_name or '(not suggested — set on the web)'}\n"
        f"Object(s): {step_run.suggested_object_names or '(none)'}\n"
        f"Description: {step_run.suggested_description or '(none)'}"
    )


def review_keyboard(step_run):
    """Inline Approve/Reject buttons for an awaiting_review WorkflowStepRun
    — callback_data parsed by app/services/telegram/worker.py's
    _handle_callback_query.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve_review:{step_run.id}"},
                {"text": "❌ Reject", "callback_data": f"reject_review:{step_run.id}"},
            ]
        ]
    }


def notify_awaiting_review(step_run):
    """Best-effort Telegram push to whoever triggered the WorkflowRun a
    step just paused in, with Approve/Reject buttons attached — see
    app/services/workflow/worker.py._start_step's
    require_review_before_build branch, the one place a WorkflowStepRun is
    ever created with status="awaiting_review". Same "never fails the
    caller" contract as notify_user(), which this delegates to once the
    triggering user is resolved.
    """
    run = step_run.run
    if not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    return notify_user(user, format_review_summary(step_run), reply_markup=review_keyboard(step_run))


BATCH_STATUS_LABELS = {"success": "succeeded", "failed": "failed", "partial_failure": "completed with failures"}


def _is_workflow_driven_batch(batch):
    """True if this BuildBatch was enqueued by a Workflow step
    (app/services/workflow/worker.py._start_step), not triggered directly
    by a human via Builders/Images. Workflow-driven batches are covered by
    the WorkflowRun-level notify_run_finished above instead — this stops a
    single workflow build step from also firing its own, redundant
    build-level notification.
    """
    return WorkflowStepRun.query.filter_by(batch_id=batch.id).first() is not None


def notify_build_started(batch):
    """Best-effort Telegram push to whoever manually triggered a BuildBatch
    (Builders/Images 'Build' button), the moment its first image actually
    starts building — see app/services/build/worker.py._claim_next_job,
    the only place a batch transitions out of "queued". See
    _is_workflow_driven_batch above for why a Workflow-triggered build is
    skipped here.
    """
    if _is_workflow_driven_batch(batch) or not batch.requested_by:
        return False

    user = User.query.get(batch.requested_by)
    text = f'Build for "{batch.version.name} {batch.full_version_string}" started.'
    return notify_user(user, text)


def notify_build_finished(batch):
    """Best-effort Telegram push to whoever manually triggered a BuildBatch
    once it reaches a terminal status — see
    app/services/build/worker.py._update_batch_status, the only place a
    batch's aggregate status is (re)computed. See
    _is_workflow_driven_batch above for why a Workflow-triggered build is
    skipped here.
    """
    if _is_workflow_driven_batch(batch) or not batch.requested_by:
        return False

    user = User.query.get(batch.requested_by)
    status_label = BATCH_STATUS_LABELS.get(batch.status, batch.status)
    text = f'Build for "{batch.version.name} {batch.full_version_string}" {status_label}.'
    return notify_user(user, text)


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


DEPLOY_ACTION_LABELS = {"deploy": "Deployment", "stop": "Stop", "restart": "Restart"}
RUN_STATUS_LABELS = {"success": "succeeded", "failed": "failed", "partial_failure": "completed with failures"}


def notify_deploy_started(run):
    """Best-effort Telegram push to whoever manually triggered a
    DeploymentRun (Deployment Manifests' Deploy/Stop/Restart buttons), the
    moment its first execution actually starts — see
    app/services/deployment/worker.py._claim_next_job. See
    _is_workflow_driven_run above for why a Workflow-triggered deploy is
    skipped here.
    """
    if _is_workflow_driven_run(run) or not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    action_label = DEPLOY_ACTION_LABELS.get(run.action, "Deployment")
    text = f'{action_label} of "{_deploy_label(run)}" started.'
    return notify_user(user, text)


def notify_deploy_finished(run):
    """Best-effort Telegram push to whoever manually triggered a
    DeploymentRun once it reaches a terminal status — see
    app/services/deployment/worker.py._update_run_status, the only place a
    run's aggregate status is (re)computed. See _is_workflow_driven_run
    above for why a Workflow-triggered deploy is skipped here.
    """
    if _is_workflow_driven_run(run) or not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    action_label = DEPLOY_ACTION_LABELS.get(run.action, "Deployment")
    status_label = RUN_STATUS_LABELS.get(run.status, run.status)
    text = f'{action_label} of "{_deploy_label(run)}" {status_label}.'
    return notify_user(user, text)


def notify_security_contact(text):
    """Best-effort Telegram alert about SOMEONE ELSE's account activity
    (wrong password, lockout, login — app/blueprints/auth/routes.py's
    login()), sent to the single User configured as
    SystemConfig.security_notification_user_id, not to the affected
    account itself. Returns False (no-op, not an error) whenever no
    recipient is configured — same "must never fail the caller" contract
    as notify_user(), which this delegates to once the recipient is
    resolved.
    """
    config = get_system_config()
    if not config.security_notification_user_id:
        return False

    contact = User.query.get(config.security_notification_user_id)
    return notify_user(contact, text)
