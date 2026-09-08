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
BUTTON_STYLE_SUCCESS = 3
BUTTON_STYLE_DANGER = 4


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


def notify_run_finished(run):
    """Discord counterpart to telegram/helpers.py's notify_run_finished —
    same trigger site (app/services/workflow/worker.py.finish_step), same
    status labeling.
    """
    if not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    status_label = {
        "success": "succeeded",
        "failed": "failed",
        "completed_with_failures": "completed with failures",
    }.get(run.status, run.status)
    content = f'Workflow "{run.workflow.name}" {status_label}.'
    return notify_user(user, content)


def review_components(step_run):
    """Approve/Reject buttons for an awaiting_review WorkflowStepRun — one
    ActionRow, two Buttons, custom_id parsed by
    app/services/discord/worker.py's interaction dispatch the same way
    Telegram's review_keyboard's callback_data is.
    """
    return [
        {
            "type": ACTION_ROW,
            "components": [
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
            ],
        }
    ]


def notify_awaiting_review(step_run):
    """Discord counterpart to telegram/helpers.py's notify_awaiting_review
    — same trigger site (app/services/workflow/worker.py._start_step's
    require_review_before_build branch), reusing the shared
    format_review_summary text verbatim.
    """
    run = step_run.run
    if not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    return notify_user(user, format_review_summary(step_run), components=review_components(step_run))


BATCH_STATUS_LABELS = {"success": "succeeded", "failed": "failed", "partial_failure": "completed with failures"}


def notify_build_started(batch):
    if _is_workflow_driven_batch(batch) or not batch.requested_by:
        return False

    user = User.query.get(batch.requested_by)
    content = f'Build for "{batch.version.name} {batch.full_version_string}" started.'
    return notify_user(user, content)


def notify_build_finished(batch):
    if _is_workflow_driven_batch(batch) or not batch.requested_by:
        return False

    user = User.query.get(batch.requested_by)
    status_label = BATCH_STATUS_LABELS.get(batch.status, batch.status)
    content = f'Build for "{batch.version.name} {batch.full_version_string}" {status_label}.'
    return notify_user(user, content)


DEPLOY_ACTION_LABELS = {"deploy": "Deployment", "stop": "Stop", "restart": "Restart"}
RUN_STATUS_LABELS = {"success": "succeeded", "failed": "failed", "partial_failure": "completed with failures"}


def notify_deploy_started(run):
    if _is_workflow_driven_run(run) or not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    action_label = DEPLOY_ACTION_LABELS.get(run.action, "Deployment")
    content = f'{action_label} of "{_deploy_label(run)}" started.'
    return notify_user(user, content)


def notify_deploy_finished(run):
    if _is_workflow_driven_run(run) or not run.triggered_by:
        return False

    user = User.query.get(run.triggered_by)
    action_label = DEPLOY_ACTION_LABELS.get(run.action, "Deployment")
    status_label = RUN_STATUS_LABELS.get(run.status, run.status)
    content = f'{action_label} of "{_deploy_label(run)}" {status_label}.'
    return notify_user(user, content)


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
