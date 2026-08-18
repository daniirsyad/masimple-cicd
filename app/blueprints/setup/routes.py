import os
import subprocess
import sys
from pathlib import Path

from flask import current_app, flash, redirect, render_template, url_for

from app.blueprints.setup import setup_bp
from app.blueprints.setup.forms import SetupForm
from app.utils.setup_status import db_connection_status, db_is_empty, is_setup_complete

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Same steps entrypoint.sh used to run unconditionally on every container
# boot — now only run when the person deploying clicks the button, and only
# if the DB isn't already at head + seeded (see is_setup_complete()).
_SETUP_STEPS = [
    [sys.executable, "-m", "flask", "db", "upgrade"],
    [sys.executable, "seeds/seed_admin.py"],
    [sys.executable, "seeds/seed_menu.py"],
    [sys.executable, "seeds/seed_change_types.py"],
    [sys.executable, "seeds/seed_version_types.py"],
    [sys.executable, "seeds/seed_ai_provider.py"],
    [sys.executable, "seeds/seed_system_config.py"],
]


def _run_setup_steps():
    """Runs migrations + seeds as subprocesses, exactly like entrypoint.sh did
    — not by importing and calling the seed scripts' run() in-process, since
    each one calls create_app() itself, which would spin up a second set of
    this app's background worker threads (build/deploy/workflow) inside the
    already-running gunicorn worker.
    """
    env = os.environ.copy()
    env.setdefault("FLASK_APP", "run.py")

    output_lines = []
    for step in _SETUP_STEPS:
        try:
            result = subprocess.run(
                step,
                cwd=str(_PROJECT_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            output_lines.append(f"$ {' '.join(step)}\nTimed out after 120s.")
            return False, "\n\n".join(output_lines)

        output_lines.append(f"$ {' '.join(step)}\n{result.stdout}{result.stderr}".strip())
        if result.returncode != 0:
            return False, "\n\n".join(output_lines)

    return True, "\n\n".join(output_lines)


@setup_bp.route("/setup", methods=["GET"])
def index():
    if is_setup_complete():
        return redirect(url_for("auth.login"))

    db_ok, db_error = db_connection_status()
    db_empty = db_is_empty() if db_ok else None
    form = SetupForm()
    return render_template(
        "setup/index.html",
        form=form,
        db_ok=db_ok,
        db_error=db_error,
        db_empty=db_empty,
    )


@setup_bp.route("/setup/run", methods=["POST"])
def run_setup():
    if is_setup_complete():
        return redirect(url_for("auth.login"))

    form = SetupForm()
    if not form.validate_on_submit():
        flash("Invalid request, please try again.", "error")
        return redirect(url_for("setup.index"))

    db_ok, db_error = db_connection_status()
    if not db_ok:
        flash(f"Can't reach the database: {db_error}", "error")
        return redirect(url_for("setup.index"))

    ok, log = _run_setup_steps()
    current_app.logger.info("Setup wizard run (%s):\n%s", "ok" if ok else "failed", log)
    if not ok:
        flash("Setup failed — check the application logs for details.", "error")
        return redirect(url_for("setup.index"))

    flash("Database setup complete. You can now log in.", "success")
    return redirect(url_for("auth.login"))
