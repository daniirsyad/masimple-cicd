from functools import wraps

from flask import abort
from flask_login import current_user


def permission_required(code):
    """Abort with 403 unless the current user's role grants the given permission code."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or not current_user.has_permission(code):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def role_required(name):
    """Abort with 403 unless the current user's role matches the given role name."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or not current_user.has_role(name):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
