from flask import has_request_context, request
from flask_login import current_user

from app.extensions import db
from app.models import ActivityLog


def log_activity(action, target_type=None, target_id=None, description=None, user=None, ip_address=None):
    """Record an entry in ActivityLog for the current user (or None if anonymous).

    `user`/`ip_address` let a caller with no Flask request context (e.g. the
    Telegram bot worker — app/services/telegram/worker.py — which only has
    an app context) attribute the entry explicitly. Every existing call
    site is unaffected: leaving both unset preserves the original
    current_user/request.remote_addr behavior, guarded by
    has_request_context() the same way app.utils.error_logger.log_error
    already guards its own request-only reads.
    """
    if user is not None:
        user_id = user.id
    elif has_request_context() and current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = None

    if ip_address is None and has_request_context():
        ip_address = request.remote_addr

    entry = ActivityLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        description=description,
        ip_address=ip_address,
    )
    db.session.add(entry)
    db.session.commit()
    return entry
