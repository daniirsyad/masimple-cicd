from app.models import User
from app.services.telegram.client import TelegramNotifier
from app.utils.crypto import decrypt
from app.utils.error_logger import log_error
from app.utils.system_config import get_system_config


def notify_user(user, text):
    """Best-effort Telegram message to this specific user's own chat —
    used only for the forgot-password flow (request + success), since only
    the account owner can act on those. For wrong-password/lockout/login
    alerts about a user's activity, see notify_security_contact() below —
    those go to the designated security contact, not to the affected user.

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
        TelegramNotifier(bot_token).send_message(user.telegram_chat_id, text)
        return True
    except Exception as exc:
        log_error(
            source="telegram.notify_user",
            exc=exc,
            description=f"Could not send Telegram notification to user '{user.username}': {exc}",
        )
        return False


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
