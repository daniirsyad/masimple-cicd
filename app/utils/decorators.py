from functools import wraps

from flask import abort, current_app
from flask_login import current_user


def _permission_denied_message(code):
    """Readable 403 reason built from the permission's description, shown by
    the app-wide 403 handler (app/__init__.py) instead of Flask's bare page."""
    from app.models import Permission

    permission = Permission.query.filter_by(code=code).first()
    if permission and permission.description:
        what = permission.description[0].lower() + permission.description[1:]
        return f"You don't have permission to {what}."
    return f"You don't have permission to do this ({code})."


def permission_required(code):
    """Redirect to login if unauthenticated (e.g. expired session); abort 403 if
    the current user's role doesn't grant the given permission code."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if not current_user.has_permission(code):
                abort(403, description=_permission_denied_message(code))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def role_required(name):
    """Redirect to login if unauthenticated (e.g. expired session); abort 403 if
    the current user's role doesn't match the given role name."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if not current_user.has_role(name):
                abort(403, description=f"This requires the {name} role.")
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
