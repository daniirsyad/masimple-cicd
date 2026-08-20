"""Telegram bot command handling — long-polls Telegram's getUpdates and lets
a linked User (User.telegram_chat_id) run and check Workflows straight from
Telegram's own "/" command menu (registered via set_my_commands below), the
same permission (`workflow.run`) and accessibility rule
(Workflow.is_accessible_to) the web UI's Run button already enforces.

Independent poll thread from the build/deploy/workflow workers — same
"start once, TESTING/reloader-parent guarded, per-iteration try/except"
shape as app/services/workflow/worker.py.
"""
import threading
import time
import uuid

from app.extensions import db
from app.models import User, Workflow, WorkflowRun, WorkflowStepRun
from app.services.telegram.client import TelegramNotifier
from app.services.telegram.helpers import format_review_summary, review_keyboard
from app.services.workflow.worker import approve_awaiting_step, enqueue_workflow_run, reject_awaiting_step
from app.utils.crypto import decrypt
from app.utils.error_logger import log_error
from app.utils.logger import log_activity
from app.utils.runtime import is_werkzeug_reloader_parent
from app.utils.system_config import get_system_config

# Telegram itself holds the connection open up to this long waiting for a
# new update, so an "enabled, nothing to do" tick already blocks for a
# while — this only matters for how quickly a *disabled* config, or a
# just-failed request, gets rechecked.
DISABLED_POLL_INTERVAL_SECONDS = 5
ERROR_RETRY_INTERVAL_SECONDS = 5

RUN_LIST_LIMIT = 10
STATUS_LIST_LIMIT = 5
REVIEW_LIST_LIMIT = 10

BOT_COMMANDS = [
    {"command": "start", "description": "Show help"},
    {"command": "run", "description": "Run a workflow"},
    {"command": "status", "description": "Check a workflow run's status"},
    {"command": "review", "description": "Review a build awaiting approval"},
]

HELP_TEXT = (
    "MASIMPLE CICD bot.\n"
    "/run - pick a workflow to run\n"
    "/status - check your recent workflow runs\n"
    "/review - review a build awaiting approval"
)

_worker_started = False
_worker_lock = threading.Lock()
# Tracks whether set_my_commands has been called since the config was last
# seen enabled — re-registers automatically if an admin disables and later
# re-enables telegram_bot_commands_enabled. Only ever touched from the
# single poll thread, so no lock needed.
_commands_registered = False

# Arbitrary but stable — just needs to not collide with any other advisory
# lock this app might someday take.
TELEGRAM_POLL_LEADER_LOCK_KEY = 917_442_101

# Keeps the leader's advisory-lock connection alive for the process's
# lifetime — see _become_poll_leader.
_leader_lock_connection = None


def _become_poll_leader(app):
    """Blocks until this process holds a Postgres advisory lock making it
    the sole Telegram getUpdates poller app-wide.

    Unlike the build/deploy/workflow workers, which are fine with several
    gunicorn worker processes concurrently polling the same DB-backed queue
    (each claim is a `SELECT ... FOR UPDATE SKIP LOCKED`, so only one wins
    per row), Telegram's getUpdates is a *global* single-consumer long-poll:
    issuing it from more than one process at once gets rejected with a 409
    ("terminated by other getUpdates request"). gunicorn runs multiple
    worker *processes* (see entrypoint.sh), each with its own Python
    interpreter, so the per-process `_worker_started` guard above isn't
    enough — every worker process still starts its own telegram-bot thread.

    Takes the lock on a dedicated connection, detached from the SQLAlchemy
    pool so it's never recycled back in or counted against pool_size, and
    kept open (via the module-level `_leader_lock_connection` reference) for
    the rest of the process's life. `pg_advisory_lock` is session-scoped,
    not transaction-scoped, so it survives the `commit()` below — if this
    process dies, Postgres releases the lock automatically and whichever
    other worker process is blocked here next becomes leader.
    """
    global _leader_lock_connection

    with app.app_context():
        raw_conn = db.engine.raw_connection()
        raw_conn.detach()
        cursor = raw_conn.cursor()
        cursor.execute("SELECT pg_advisory_lock(%s)", (TELEGRAM_POLL_LEADER_LOCK_KEY,))
        cursor.close()
        raw_conn.commit()

    _leader_lock_connection = raw_conn


def _resolve_user(chat_id):
    return User.query.filter_by(telegram_chat_id=str(chat_id)).first()


def _reply(notifier, chat_id, text, reply_markup=None):
    try:
        notifier.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as exc:
        log_error(
            source="telegram.worker.send_message",
            exc=exc,
            description=f"Could not send Telegram reply: {exc}",
        )


def _answer(notifier, callback_query_id, text=None):
    try:
        notifier.answer_callback_query(callback_query_id, text=text)
    except Exception as exc:
        log_error(
            source="telegram.worker.answer_callback_query",
            exc=exc,
            description=f"Could not answer Telegram callback query: {exc}",
        )


def _workflow_keyboard(workflows):
    return {
        "inline_keyboard": [[{"text": workflow.name, "callback_data": f"run:{workflow.id}"}] for workflow in workflows]
    }


def _run_keyboard(runs):
    return {
        "inline_keyboard": [
            [{"text": f"{run.workflow.name} ({run.status})", "callback_data": f"status:{run.id}"}] for run in runs
        ]
    }


def _handle_run_command(notifier, chat_id, user):
    if not user.has_permission("workflow.run"):
        _reply(notifier, chat_id, "You don't have permission to run workflows.")
        return

    workflows = [
        workflow
        for workflow in Workflow.query.filter_by(is_active=True).order_by(Workflow.name).all()
        if workflow.is_accessible_to(user)
    ][:RUN_LIST_LIMIT]

    if not workflows:
        _reply(notifier, chat_id, "No workflows available to run.")
        return

    _reply(notifier, chat_id, "Pick a workflow to run:", reply_markup=_workflow_keyboard(workflows))


def _handle_status_command(notifier, chat_id, user):
    runs = (
        WorkflowRun.query.filter_by(triggered_by=user.id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(STATUS_LIST_LIMIT)
        .all()
    )
    if not runs:
        _reply(notifier, chat_id, "No workflow runs yet.")
        return

    _reply(notifier, chat_id, "Pick a run to check:", reply_markup=_run_keyboard(runs))


def _review_keyboard_for_list(step_runs):
    return {
        "inline_keyboard": [
            [
                {
                    "text": f"{step_run.run.workflow.name} (step {step_run.step_order})",
                    "callback_data": f"review:{step_run.id}",
                }
            ]
            for step_run in step_runs
        ]
    }


def _handle_review_command(notifier, chat_id, user):
    # Same authorization as the web review panel
    # (workflows.routes._awaiting_review_step_run_or_404): any workflow.run
    # holder who can see the workflow, not just whoever triggered the run —
    # a shared review queue, unlike /status's "your own runs only" scope.
    if not user.has_permission("workflow.run"):
        _reply(notifier, chat_id, "You don't have permission to review workflow builds.")
        return

    step_runs = [
        step_run
        for step_run in WorkflowStepRun.query.filter_by(status="awaiting_review")
        .order_by(WorkflowStepRun.created_at.desc())
        .all()
        if step_run.run.workflow.is_accessible_to(user)
    ][:REVIEW_LIST_LIMIT]

    if not step_runs:
        _reply(notifier, chat_id, "Nothing awaiting review.")
        return

    _reply(notifier, chat_id, "Pick a build to review:", reply_markup=_review_keyboard_for_list(step_runs))


def _handle_message(notifier, message):
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if not text:
        return
    # Group chats append the bot's own username, e.g. "/run@my_bot".
    command = text.split()[0].split("@")[0].lower()

    user = _resolve_user(chat_id)
    if user is None:
        if command in ("/start", "/run", "/status", "/review"):
            _reply(
                notifier,
                chat_id,
                "Your Telegram account isn't linked. Ask an admin to set your Chat ID in Account Settings.",
            )
        return

    if command == "/start":
        _reply(notifier, chat_id, HELP_TEXT)
    elif command == "/run":
        _handle_run_command(notifier, chat_id, user)
    elif command == "/status":
        _handle_status_command(notifier, chat_id, user)
    elif command == "/review":
        _handle_review_command(notifier, chat_id, user)
    else:
        _reply(notifier, chat_id, "Unrecognized command. Try /run, /status, or /review.")


def _handle_run_callback(notifier, callback_query_id, chat_id, user, workflow_id):
    if not user.has_permission("workflow.run"):
        _answer(notifier, callback_query_id, "You don't have permission to run workflows.")
        return

    workflow = Workflow.query.get(workflow_id)
    if workflow is None or not workflow.is_active or not workflow.is_accessible_to(user):
        _answer(notifier, callback_query_id, "That workflow is no longer available.")
        return

    run = enqueue_workflow_run(workflow, triggered_by=user.id)
    log_activity(
        action="RUN_WORKFLOW",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Workflow '{workflow.name}' run via Telegram by {user.username}",
        user=user,
    )
    _answer(notifier, callback_query_id, "Run queued.")
    _reply(notifier, chat_id, f'Queued a run of "{workflow.name}". Use /status to check progress.')


def _format_run_status(run):
    lines = [f'Workflow "{run.workflow.name}" — {run.status}']
    for step_run in run.step_runs.all():
        line = f"  Step {step_run.step_order} ({step_run.step_type}): {step_run.status}"
        if step_run.error:
            line += f" — {step_run.error}"
        lines.append(line)
    return "\n".join(lines)


def _handle_status_callback(notifier, callback_query_id, chat_id, user, run_id):
    # Deliberately narrower than Workflow.is_accessible_to: only a run this
    # user personally triggered, not every run of every workflow they can
    # view — avoids exposing other users' run details through Telegram.
    run = WorkflowRun.query.get(run_id)
    if run is None or run.triggered_by != user.id:
        _answer(notifier, callback_query_id, "Run not found.")
        return

    _answer(notifier, callback_query_id)
    _reply(notifier, chat_id, _format_run_status(run))


def _awaiting_review_step_run_for_user(step_run_id, user):
    """Same rule as workflows.routes._awaiting_review_step_run_or_404: must
    still be awaiting_review (an approve/reject callback tapped twice, or
    tapped after someone else already acted on it, is a no-op not a
    crash), and the reviewer must be able to see the workflow. Returns
    None if either check fails.
    """
    step_run = WorkflowStepRun.query.get(step_run_id)
    if step_run is None or step_run.status != "awaiting_review":
        return None
    if not step_run.run.workflow.is_accessible_to(user):
        return None
    return step_run


def _handle_review_detail_callback(notifier, callback_query_id, chat_id, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _answer(notifier, callback_query_id, "You don't have permission to review workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _answer(notifier, callback_query_id, "This review is no longer pending.")
        return

    _answer(notifier, callback_query_id)
    _reply(notifier, chat_id, format_review_summary(step_run), reply_markup=review_keyboard(step_run))


def _handle_approve_review_callback(notifier, callback_query_id, chat_id, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _answer(notifier, callback_query_id, "You don't have permission to approve workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _answer(notifier, callback_query_id, "This review is no longer pending.")
        return

    # Telegram has no form to collect a missing Version Bump/Change Type
    # the way the web review panel's dropdowns do — approve as-is only
    # when the AI/heuristic suggestion already has both.
    if not step_run.suggested_bump_type or not step_run.suggested_change_type_id:
        _answer(notifier, callback_query_id, "Missing Version Bump/Change Type — approve from the web UI instead.")
        return

    run = step_run.run
    try:
        approve_awaiting_step(
            step_run,
            bump_type=step_run.suggested_bump_type,
            change_type_id=step_run.suggested_change_type_id,
            object_names_text=step_run.suggested_object_names,
            description=step_run.suggested_description,
            requested_by=user.id,
        )
    except ValueError as exc:
        _answer(notifier, callback_query_id, str(exc))
        return

    log_activity(
        action="APPROVE_WORKFLOW_BUILD_STEP",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Approved AI-suggested build metadata for a step in workflow '{run.workflow.name}' via Telegram by {user.username}",
        user=user,
    )
    _answer(notifier, callback_query_id, "Approved.")
    _reply(notifier, chat_id, f'Approved and queued the build for "{run.workflow.name}".')


def _handle_reject_review_callback(notifier, callback_query_id, chat_id, user, step_run_id):
    if not user.has_permission("workflow.run"):
        _answer(notifier, callback_query_id, "You don't have permission to reject workflow builds.")
        return

    step_run = _awaiting_review_step_run_for_user(step_run_id, user)
    if step_run is None:
        _answer(notifier, callback_query_id, "This review is no longer pending.")
        return

    run = step_run.run
    reject_awaiting_step(step_run)
    log_activity(
        action="REJECT_WORKFLOW_BUILD_STEP",
        target_type="workflow_run",
        target_id=str(run.id),
        description=f"Rejected AI-suggested build metadata for a step in workflow '{run.workflow.name}' via Telegram by {user.username}",
        user=user,
    )
    _answer(notifier, callback_query_id, "Rejected.")
    _reply(notifier, chat_id, f'Rejected the build step for "{run.workflow.name}".')


def _handle_callback_query(notifier, callback_query):
    callback_query_id = callback_query["id"]
    chat_id = callback_query["message"]["chat"]["id"]
    data = callback_query.get("data") or ""

    user = _resolve_user(chat_id)
    if user is None:
        _answer(notifier, callback_query_id, "Your Telegram account isn't linked.")
        return

    action, _, raw_id = data.partition(":")
    try:
        target_id = uuid.UUID(raw_id)
    except ValueError:
        _answer(notifier, callback_query_id, "Invalid selection.")
        return

    if action == "run":
        _handle_run_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "status":
        _handle_status_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "review":
        _handle_review_detail_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "approve_review":
        _handle_approve_review_callback(notifier, callback_query_id, chat_id, user, target_id)
    elif action == "reject_review":
        _handle_reject_review_callback(notifier, callback_query_id, chat_id, user, target_id)
    else:
        _answer(notifier, callback_query_id, "Unrecognized action.")


def _handle_update(notifier, update):
    if "message" in update:
        _handle_message(notifier, update["message"])
    elif "callback_query" in update:
        _handle_callback_query(notifier, update["callback_query"])


def _tick(app):
    """Returns True if a real long-poll to Telegram happened (so the caller
    doesn't need to add its own delay before the next tick), False if this
    tick was skipped (bot commands disabled or no token configured).
    """
    global _commands_registered

    with app.app_context():
        config = get_system_config()
        if not config.telegram_bot_commands_enabled or not config.encrypted_telegram_bot_token:
            _commands_registered = False
            db.session.remove()
            return False

        bot_token = decrypt(config.encrypted_telegram_bot_token)
        if not bot_token:
            db.session.remove()
            return False

        notifier = TelegramNotifier(bot_token)

        if not _commands_registered:
            try:
                notifier.set_my_commands(BOT_COMMANDS)
                _commands_registered = True
            except Exception as exc:
                log_error(
                    source="telegram.worker.set_my_commands",
                    exc=exc,
                    description=f"Could not register the Telegram bot command menu: {exc}",
                )

        offset = (config.telegram_last_update_id or 0) + 1
        updates = notifier.get_updates(offset=offset)

        for update in updates:
            try:
                _handle_update(notifier, update)
            except Exception as exc:
                # Roll back so a broken transaction from a bad update
                # doesn't also block persisting the offset below — an
                # unhandled failure here must still advance past the
                # offending update, or it would be redelivered and retried
                # forever on every future tick.
                db.session.rollback()
                log_error(
                    source="telegram.worker.handle_update",
                    exc=exc,
                    description=f"Could not handle Telegram update {update.get('update_id')}: {exc}",
                )
            config.telegram_last_update_id = update["update_id"]
            db.session.commit()

        db.session.remove()
        return True


def _run(app):
    """Thread entry point: block until this process is the sole Telegram
    poll leader, then start polling. See _become_poll_leader for why this
    (unlike the build/deploy/workflow workers) can't just let every gunicorn
    worker process poll concurrently.
    """
    _become_poll_leader(app)
    _poll_loop(app)


def _poll_loop(app):
    while True:
        try:
            polled = _tick(app)
        except Exception as exc:
            with app.app_context():
                log_error(
                    source="telegram.worker.poll_loop",
                    exc=exc,
                    description=f"Telegram bot poll loop iteration failed: {exc}",
                )
            time.sleep(ERROR_RETRY_INTERVAL_SECONDS)
            continue

        if not polled:
            time.sleep(DISABLED_POLL_INTERVAL_SECONDS)


def start_worker(app):
    """Start this process's background Telegram-bot poll thread, once.

    Same TESTING-skip and Werkzeug-reloader guards as the build/deploy/
    workflow workers' own start_worker — see app/services/build/worker.py
    for why.
    """
    global _worker_started

    if app.config.get("TESTING"):
        return

    if is_werkzeug_reloader_parent(app):
        return

    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    thread = threading.Thread(target=_run, args=(app,), daemon=True, name="telegram-bot")
    thread.start()
