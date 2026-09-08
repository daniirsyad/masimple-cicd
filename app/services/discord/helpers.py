from flask import current_app, url_for

from app.models import User
from app.services.bot_shared import _deploy_label, _is_workflow_driven_batch, _is_workflow_driven_run, format_review_summary
from app.services.discord.client import DiscordClient
from app.utils.crypto import decrypt
from app.utils.error_logger import log_error
from app.utils.system_config import get_system_config

# Discord component type/style constants — see
# https://discord.com/developers/docs/interactions/message-components
ACTION_ROW = 1
BUTTON = 2
BUTTON_STYLE_PRIMARY = 1
BUTTON_STYLE_SUCCESS = 3
BUTTON_STYLE_DANGER = 4
BUTTON_STYLE_LINK = 5


def notify_user(user, content, components=None):
    """Best-effort Discord DM to this specific user — mirrors
    app/services/telegram/helpers.py's notify_user exactly (same guard
    clauses, same "never raises, logs+swallows, returns bool" contract),
    substituting User.discord_user_id/SystemConfig.discord_* for the
    Telegram equivalents and DiscordClient.send_dm_message for
    TelegramNotifier.send_message.
    """
    if not user or not user.discord_user_id:
        return False

    config = get_system_config()
    if not config.discord_notifications_enabled:
        return False

    try:
        bot_token = decrypt(config.encrypted_discord_bot_token)
        if not bot_token:
            return False
        DiscordClient(bot_token).send_dm_message(user.discord_user_id, content, components=components)
        return True
    except Exception as exc:
        log_error(
            source="discord.notify_user",
            exc=exc,
            description=f"Could not send Discord notification to user '{user.username}': {exc}",
        )
        return False


def _external_url(endpoint, **values):
    """Builds a real, absolute link to a page in this same web app from
    inside a background worker thread — there's no active HTTP request to
    hang a URL adapter off of, and SystemConfig.app_base_url (see its own
    docstring for why this exists instead of a global SERVER_NAME) is the
    only way to know this app's own public host. Returns None (never
    raises) when it isn't configured, or if URL building fails for any
    other reason — every caller treats a None link as "just omit the
    button," never as an error.
    """
    config = get_system_config()
    if not config.app_base_url:
        return None
    try:
        with current_app.test_request_context(base_url=config.app_base_url):
            return url_for(endpoint, _external=True, **values)
    except Exception as exc:
        log_error(
            source="discord._external_url",
            exc=exc,
            description=f"Could not build an external URL for endpoint '{endpoint}': {exc}",
        )
        return None


def _action_row(*buttons):
    return {"type": ACTION_ROW, "components": list(buttons)}


def _link_button(label, url):
    return {"type": BUTTON, "style": BUTTON_STYLE_LINK, "label": label, "url": url}


def status_components(run_id):
    """A single 'Check Status' Button — a real interactive component
    (custom_id, not a Link), reusing the exact same status:<uuid> action
    token /status's own select menu already dispatches through (see
    _dispatch_component in app/services/discord/worker.py) — tapping it
    shows the run's status right there in Discord, no typing or web
    round-trip needed.
    """
    return [_action_row({"type": BUTTON, "style": BUTTON_STYLE_PRIMARY, "label": "Check Status", "custom_id": f"status:{run_id}"})]


def notify_run_finished(run):
    """Discord counterpart to telegram/helpers.py's notify_run_finished —
    same trigger site (app/services/workflow/worker.py.finish_step), same
    status labeling, plus a Check Status button so there's nothing to type.
    """
    if not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    status_label = {
        "success": "succeeded",
        "failed": "failed",
        "completed_with_failures": "completed with failures",
    }.get(run.status, run.status)
    content = f'Your workflow **{run.workflow.name}** just {status_label}!'
    return notify_user(user, content, components=status_components(run.id))


def review_components(step_run):
    """Approve/Reject buttons for an awaiting_review WorkflowStepRun — one
    ActionRow, two Buttons, custom_id parsed by
    app/services/discord/worker.py's interaction dispatch the same way
    Telegram's review_keyboard's callback_data is.
    """
    return [
        _action_row(
            {
                "type": BUTTON,
                "style": BUTTON_STYLE_SUCCESS,
                "label": "Approve",
                "custom_id": f"approve_review:{step_run.id}",
            },
            {
                "type": BUTTON,
                "style": BUTTON_STYLE_DANGER,
                "label": "Reject",
                "custom_id": f"reject_review:{step_run.id}",
            },
        )
    ]


def notify_awaiting_review(step_run):
    """Discord counterpart to telegram/helpers.py's notify_awaiting_review
    — same trigger site (app/services/workflow/worker.py._start_step's
    require_review_before_build branch), reusing the shared
    format_review_summary text verbatim (it's shared with Telegram — only
    the friendly wrapper line and the extra "View in app" button here are
    Discord-specific). The web review panel can do more than Discord's
    approve-as-is-only buttons (edit a Version Bump/Change Type the AI
    didn't confidently suggest), so the link is genuinely useful, not just
    a nicety.
    """
    run = step_run.run
    if not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    content = f'**{run.workflow.name}** needs a quick look before it can build:\n\n{format_review_summary(step_run)}'

    components = review_components(step_run)
    link = _external_url("workflows.view_run", run_id=run.id)
    if link:
        components = components + [_action_row(_link_button("View in app", link))]

    return notify_user(user, content, components=components)


BATCH_STATUS_LABELS = {"success": "succeeded", "failed": "failed", "partial_failure": "completed with failures"}


def notify_build_started(batch):
    if _is_workflow_driven_batch(batch) or not batch.requested_by:
        return False

    user = User.query.get(batch.requested_by)
    content = f'Build started for **{batch.version.name} {batch.full_version_string}** — hang tight!'
    return notify_user(user, content)


def notify_build_finished(batch):
    if _is_workflow_driven_batch(batch) or not batch.requested_by:
        return False

    user = User.query.get(batch.requested_by)
    status_label = BATCH_STATUS_LABELS.get(batch.status, batch.status)
    content = f'Build for **{batch.version.name} {batch.full_version_string}** {status_label}.'

    components = None
    link = _external_url("images.list_images")
    if link:
        components = [_action_row(_link_button("View Details", link))]

    return notify_user(user, content, components=components)


DEPLOY_ACTION_LABELS = {"deploy": "Deployment", "stop": "Stop", "restart": "Restart"}
RUN_STATUS_LABELS = {"success": "succeeded", "failed": "failed", "partial_failure": "completed with failures"}


def notify_deploy_started(run):
    if _is_workflow_driven_run(run) or not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    action_label = DEPLOY_ACTION_LABELS.get(run.action, "Deployment")
    content = f'{action_label} started for **{_deploy_label(run)}** — hang tight!'
    return notify_user(user, content)


def notify_deploy_finished(run):
    if _is_workflow_driven_run(run) or not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    action_label = DEPLOY_ACTION_LABELS.get(run.action, "Deployment")
    status_label = RUN_STATUS_LABELS.get(run.status, run.status)
    content = f'{action_label} of **{_deploy_label(run)}** {status_label}.'

    components = None
    link = _external_url("deployment_runs.detail", run_id=run.id)
    if link:
        components = [_action_row(_link_button("View Details", link))]

    return notify_user(user, content, components=components)


def notify_security_contact(text):
    """Discord counterpart to telegram/helpers.py's notify_security_contact
    — targets the SAME shared SystemConfig.security_notification_user_id
    Telegram's version does; a no-op here if that user has no
    discord_user_id set or Discord notifications aren't enabled, same as
    Telegram's version is a no-op if that user has no telegram_chat_id.
    """
    config = get_system_config()
    if not config.security_notification_user_id:
        return False

    contact = User.query.get(config.security_notification_user_id)
    return notify_user(contact, text)
