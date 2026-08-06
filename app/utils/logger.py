from flask import request
from flask_login import current_user

from app.extensions import db
from app.models import ActivityLog


def log_activity(action, target_type=None, target_id=None, description=None):
    """Record an entry in ActivityLog for the current user (or None if anonymous)."""
    user_id = current_user.id if current_user.is_authenticated else None

    entry = ActivityLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        description=description,
        ip_address=request.remote_addr,
    )
    db.session.add(entry)
    db.session.commit()
    return entry
