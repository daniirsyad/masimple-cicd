from functools import wraps

from flask import abort, current_app
from flask_login import current_user


def permission_required(code):
    """Redirect to login if unauthenticated (e.g. expired session); abort 403 if
    the current user's role doesn't grant the given permission code."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if not current_user.has_permission(code):
                abort(403)
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
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
