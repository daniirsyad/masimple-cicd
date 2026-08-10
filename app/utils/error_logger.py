import traceback as tb_module

from flask import has_request_context, request, url_for
from flask_login import current_user
from markupsafe import Markup, escape

from app.extensions import db
from app.models import ErrorLog


def log_error(source, exc=None, description=None, detail=None):
    """Record an entry in ErrorLog for an exception (or an arbitrary failure
    with no exception object, e.g. a provider returning an error string).

    `detail` covers failures that have no Python exception to pull a
    traceback from but still have useful diagnostic text (e.g. a build
    engine's full log for a build that failed cleanly, without raising) — it
    goes in the same `traceback` column so the UI shows it the same way. If
    both `exc` and `detail` are given, the exception's traceback wins.

    Safe to call from anywhere, including a background thread with no
    request context (the build worker). Doesn't roll back the caller's
    session up front — a caller may still have unrelated pending writes it
    wants preserved — but if the insert itself fails because the session was
    already left in a broken state (e.g. an uncleared IntegrityError), it
    rolls back and retries once rather than raising out of an error logger.
    """
    user_id = None
    method = path = ip_address = None
    if has_request_context():
        if current_user.is_authenticated:
            user_id = current_user.id
        method = request.method
        path = request.path
        ip_address = request.remote_addr

    message = description or (str(exc) if exc is not None else "Unknown error")
    traceback_text = None
    if isinstance(exc, BaseException):
        traceback_text = "".join(tb_module.format_exception(type(exc), exc, exc.__traceback__))
    elif detail:
        traceback_text = detail

    entry = ErrorLog(
        user_id=user_id,
        source=source,
        message=message,
        traceback=traceback_text,
        method=method,
        path=path,
        ip_address=ip_address,
    )
    db.session.add(entry)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        db.session.add(entry)
        db.session.commit()
    return entry


def error_detail_link(message, entry):
    """`message` plus a real link straight to the ErrorLog row `log_error`
    just created — the same "See Error Logs for details" flashes/inline
    banners used to say, now pointing at the specific row instead of making
    the user go find it themselves.

    Returns a `Markup` (Jinja-safe) string, not a plain str — `message` is
    the only piece of this call site's own text mixed in here, so it's the
    only thing that gets escaped; the link's href comes only from
    `entry.id` (a UUID) via `url_for()`, never from request/user input.
    Safe to hand directly to `flash()` or to a template `error` variable:
    both are rendered via a bare `{{ }}`, and Jinja/MarkupSafe skip their
    own escaping for anything that's already `Markup`.
    """
    link = url_for("logs.error_detail", error_id=entry.id)
    return Markup(f'{escape(message)} <a href="{link}" target="_blank" class="link link-primary">View error details</a>')
