"""Workflow orchestrator — an independent poll thread from both the build
and deploy workers. It never executes a build or a deploy itself: each step
is just a normal BuildBatch/DeploymentRun, enqueued into the existing
build/deploy queues (see enqueue_build_batch/enqueue_deployment_run) and
executed by their own worker threads exactly as a manual trigger would be.
This loop only watches that BuildBatch/DeploymentRun's status and, once it
reaches a terminal state, decides whether to advance to the WorkflowStep's
next step or stop the run — so a WorkflowRun never competes with a manual
trigger or another workflow for either queue's single-flight slot.
"""
import threading
import time
from datetime import datetime

from app.extensions import db
from app.models import BuildBatch, DeploymentRun, Object, Version, WorkflowRun, WorkflowStep, WorkflowStepRun
from app.services.build.prefill import compute_build_prefill
from app.services.build.worker import enqueue_build_batch
from app.services.deployment.worker import enqueue_deployment_run
from app.services.discord.helpers import notify_awaiting_review as discord_notify_awaiting_review
from app.services.discord.helpers import notify_run_finished as discord_notify_run_finished
from app.services.telegram.helpers import notify_awaiting_review, notify_run_finished
from app.services.workflow.resolver import resolve_step_builders, resolve_step_manifests
from app.utils.error_logger import log_error
from app.utils.runtime import is_werkzeug_reloader_parent

POLL_INTERVAL_SECONDS = 2

BUILD_TERMINAL_STATUSES = {"success", "failed", "partial_failure"}
DEPLOY_TERMINAL_STATUSES = {"success", "failed", "partial_failure"}

_worker_started = False
_worker_lock = threading.Lock()


def enqueue_workflow_run(workflow, triggered_by):
    """Creates one WorkflowRun, queued for the orchestrator to pick up on its
    next tick — mirrors enqueue_build_batch/enqueue_deployment_run's "just
    insert a row and let the worker do the rest" shape.
    """
    run = WorkflowRun(workflow_id=workflow.id, status="queued", triggered_by=triggered_by)
    db.session.add(run)
    db.session.commit()
    return run


def _first_step(workflow_id):
    return WorkflowStep.query.filter_by(workflow_id=workflow_id).order_by(WorkflowStep.order).first()


def _next_step(step):
    return (
        WorkflowStep.query.filter_by(workflow_id=step.workflow_id)
        .filter(WorkflowStep.order > step.order)
        .order_by(WorkflowStep.order)
        .first()
    )


def finish_step(run, step, success):
    """Shared branch point for a step that just reached a terminal outcome —
    whether that's a real BuildBatch/DeploymentRun finishing (see
    _check_current_step) or a step that couldn't even be started (see
    _fail_step). Decides whether the run stops here or moves on.
    """
    if not success:
        run.has_failed_step = True
        if step.on_failure == "stop":
            run.status = "failed"
            run.finished_at = datetime.utcnow()
            db.session.commit()
            notify_run_finished(run)
            discord_notify_run_finished(run)
            return

    next_step = _next_step(step)
    if next_step is None:
        run.status = "completed_with_failures" if run.has_failed_step else "success"
        run.finished_at = datetime.utcnow()
        db.session.commit()
        notify_run_finished(run)
        discord_notify_run_finished(run)
    else:
        db.session.commit()
        _start_step(run, next_step)


def approve_awaiting_step(step_run, bump_type, change_type_id, object_names_text, description, requested_by):
    """Applies a human-reviewed set of build values to an awaiting_review
    WorkflowStepRun and enqueues the real BuildBatch — the shared approval
    logic behind both the web review panel
    (workflows.routes.approve_step_run) and Telegram's approve_review
    callback (app/services/telegram/worker.py). Raises ValueError if the
    step's builders no longer resolve to anything (its group/individual
    selection changed since the run started) — callers turn that into
    whatever user-facing message fits their channel.
    """
    step = step_run.step
    builders = resolve_step_builders(step)
    if not builders:
        raise ValueError("This step's builders no longer resolve to anything.")

    version = Version.query.get({builder.version_id for builder in builders}.pop())
    object_names = [name.strip() for name in (object_names_text or "").split(",") if name.strip()]

    batch = enqueue_build_batch(
        version=version,
        bump_type=bump_type,
        builder_branches=[(builder, builder.default_branch) for builder in builders],
        objects=Object.resolve([], object_names) if object_names else [],
        additional_description=(description or "").strip() or None,
        requested_by=requested_by,
        change_type_id=change_type_id,
    )
    step_run.status = "running"
    step_run.batch_id = batch.id
    db.session.commit()
    return batch


def reject_awaiting_step(step_run, reason="Rejected by reviewer."):
    """Fails an awaiting_review WorkflowStepRun without enqueueing anything
    and advances the run via finish_step — shared by the web review
    panel's reject route and Telegram's reject_review callback.
    """
    run = step_run.run
    step = step_run.step
    step_run.status = "failed"
    step_run.error = reason
    step_run.finished_at = datetime.utcnow()
    db.session.commit()
    finish_step(run, step, success=False)


def _fail_step(run, step, message):
    """A step that can't even be started — its group(s)/items resolve to
    nothing, or a build step's resolved builders don't share one Version or
    are missing a default branch. Recorded as a WorkflowStepRun the same as
    any other failed step, just with no BuildBatch/DeploymentRun behind it,
    so run history always shows why.
    """
    step_run = WorkflowStepRun(
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        step_order=step.order,
        step_type=step.step_type,
        status="failed",
        error=message,
        finished_at=datetime.utcnow(),
    )
    db.session.add(step_run)
    finish_step(run, step, success=False)


def _start_step(run, step):
    run.current_step_id = step.id
    run.status = "running"
    if run.started_at is None:
        run.started_at = datetime.utcnow()

    if step.step_type == "build":
        builders = resolve_step_builders(step)
        if not builders:
            db.session.commit()
            _fail_step(run, step, "No builders resolved for this step (its selected group(s)/items are empty).")
            return

        version_ids = {builder.version_id for builder in builders}
        if len(version_ids) > 1:
            db.session.commit()
            _fail_step(run, step, "The resolved builders don't all share the same Version.")
            return

        missing_branch = [builder.name for builder in builders if not builder.default_branch]
        if missing_branch:
            db.session.commit()
            _fail_step(run, step, f"No default branch configured for: {', '.join(missing_branch)}.")
            return

        version = Version.query.get(version_ids.pop())
        builder_branches = [(builder, builder.default_branch) for builder in builders]

        if step.auto_generate_build_metadata:
            prefill = compute_build_prefill(builder_branches, additional_description=step.additional_description)
            object_names = [obj.name for obj in prefill["matched_objects"]] + prefill["new_object_names"]

            if step.require_review_before_build:
                # Same "never apply raw AI output unseen" rule
                # compute_build_prefill() itself already follows — stash the
                # suggestion and stop here without enqueueing anything; the
                # poll loop's own _check_current_step() already no-ops on any
                # step_run.status != "running", so this run just waits safely
                # until workflows.routes.approve_step_run/reject_step_run
                # acts on it (see WorkflowStepRun's own docstring).
                step_run = WorkflowStepRun(
                    workflow_run_id=run.id,
                    workflow_step_id=step.id,
                    step_order=step.order,
                    step_type=step.step_type,
                    status="awaiting_review",
                    suggested_bump_type=prefill["bump_type"],
                    suggested_change_type_id=prefill["change_type_id"],
                    suggested_object_names=", ".join(object_names) or None,
                    suggested_description=prefill["description"] or None,
                )
                db.session.add(step_run)
                db.session.commit()
                notify_awaiting_review(step_run)
                discord_notify_awaiting_review(step_run)
                return

            bump_type = prefill["bump_type"]
            change_type_id = prefill["change_type_id"]
            objects = Object.resolve([], object_names) if object_names else []
            description = prefill["description"] or step.additional_description
        else:
            bump_type = step.bump_type
            change_type_id = step.change_type_id
            # WorkflowStep.object is still a single free-typed string
            # captured at authoring time (see WorkflowStep's own docstring)
            # — resolved as a "new" name on every run rather than
            # multi-object yet, same get-or-create as any other
            # Object.resolve() call site, so it collapses onto the same real
            # Object row a manual trigger using the same text would.
            objects = Object.resolve([], [step.object])
            description = step.additional_description

        batch = enqueue_build_batch(
            version=version,
            bump_type=bump_type,
            builder_branches=builder_branches,
            objects=objects,
            additional_description=description,
            requested_by=run.triggered_by,
            change_type_id=change_type_id,
        )
        step_run = WorkflowStepRun(
            workflow_run_id=run.id,
            workflow_step_id=step.id,
            step_order=step.order,
            step_type=step.step_type,
            status="running",
            batch_id=batch.id,
        )
    else:
        manifests = resolve_step_manifests(step)
        if not manifests:
            db.session.commit()
            _fail_step(run, step, "No manifests resolved for this step (its selected group(s)/items are empty).")
            return

        deployment_run, _execution_count = enqueue_deployment_run(manifests=manifests, triggered_by=run.triggered_by)
        step_run = WorkflowStepRun(
            workflow_run_id=run.id,
            workflow_step_id=step.id,
            step_order=step.order,
            step_type=step.step_type,
            status="running",
            deployment_run_id=deployment_run.id,
        )

    db.session.add(step_run)
    db.session.commit()


def _check_current_step(run):
    step_run = (
        WorkflowStepRun.query.filter_by(workflow_run_id=run.id, workflow_step_id=run.current_step_id)
        .order_by(WorkflowStepRun.created_at.desc())
        .first()
    )
    if step_run is None or step_run.status != "running":
        return

    if step_run.batch_id is not None:
        batch = BuildBatch.query.get(step_run.batch_id)
        underlying_status = batch.status if batch else "failed"
        terminal = underlying_status in BUILD_TERMINAL_STATUSES
    else:
        deployment_run = DeploymentRun.query.get(step_run.deployment_run_id)
        underlying_status = deployment_run.status if deployment_run else "failed"
        terminal = underlying_status in DEPLOY_TERMINAL_STATUSES

    if not terminal:
        return

    success = underlying_status == "success"
    step_run.status = "success" if success else "failed"
    step_run.finished_at = datetime.utcnow()
    db.session.commit()

    step = WorkflowStep.query.get(step_run.workflow_step_id)
    finish_step(run, step, success)


def _tick(app):
    """Runs one poll iteration. Both queries below use SELECT ... FOR UPDATE
    SKIP LOCKED (same pattern as app/services/build/worker.py's
    _claim_next_job/app/services/deployment/worker.py's own claim query) —
    gunicorn runs multiple worker *processes* (see entrypoint.sh's
    `--workers 3`), each starting its own workflow-orchestrator thread
    (start_worker's _worker_started guard is a process-local global, so it
    only stops a second thread in the *same* process, not the other
    processes' own threads — identical root cause to the Telegram
    getUpdates 409 bug this app already hit once before). Without row
    locking here, two/three processes' threads could all see the same
    WorkflowRun as "queued" (or "running", mid-step) in the same 2-second
    window and each call _start_step/_check_current_step on it, creating
    duplicate WorkflowStepRuns and duplicate BuildBatches/DeploymentRuns
    for the same step. SKIP LOCKED means a process that loses the race
    simply skips that row this tick rather than blocking or double-acting
    on it — by its next tick the row's status has already moved on.
    """
    with app.app_context():
        queued_runs = WorkflowRun.query.filter_by(status="queued").with_for_update(skip_locked=True).all()
        for run in queued_runs:
            step = _first_step(run.workflow_id)
            if step is None:
                run.status = "success"
                run.started_at = datetime.utcnow()
                run.finished_at = datetime.utcnow()
                db.session.commit()
                notify_run_finished(run)
                discord_notify_run_finished(run)
            else:
                _start_step(run, step)

        running_runs = WorkflowRun.query.filter_by(status="running").with_for_update(skip_locked=True).all()
        for run in running_runs:
            _check_current_step(run)

        db.session.remove()


def _poll_loop(app):
    while True:
        try:
            _tick(app)
        except Exception as exc:
            with app.app_context():
                log_error(
                    source="workflow.worker.poll_loop",
                    exc=exc,
                    description=f"Workflow orchestrator poll loop iteration failed: {exc}",
                )
        time.sleep(POLL_INTERVAL_SECONDS)


def start_worker(app):
    """Start this process's background workflow-orchestrator thread, once.

    Same TESTING-skip and Werkzeug-reloader guards as the build/deploy
    workers' own start_worker — see app/services/build/worker.py for why.
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

    thread = threading.Thread(target=_poll_loop, args=(app,), daemon=True, name="workflow-orchestrator")
    thread.start()
