import os
import sys


def is_werkzeug_reloader_parent(app):
    """True only for Werkzeug's own dev-server reloader *parent* process —
    the one that watches for file changes and re-execs a child rather than
    ever serving a real request itself (the child gets WERKZEUG_RUN_MAIN=true
    set on it; the parent never does). Background worker threads (build,
    deploy, workflow — see their start_worker()s) must not start in that
    parent, since it's about to be killed/replaced and would otherwise leave
    a second, orphaned poller running.

    `app.debug` alone is NOT enough to detect this — it's just a Flask config
    value with no bearing on which WSGI server is running the app. Under
    gunicorn (this app's actual deployment target), app.debug can easily be
    True too (e.g. FLASK_ENV=development in .env), but there is no reloader
    and no doomed parent process, so the threads must start for real. Since
    gunicorn always imports its own `gunicorn` package into the process
    that's actually serving requests, checking for it rules out gunicorn
    workers being mistaken for a Werkzeug reloader parent.
    """
    return app.debug and "gunicorn" not in sys.modules and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
