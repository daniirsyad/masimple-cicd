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
import os
import threading
import time
from datetime import datetime

from app.extensions import db
from app.models import BuildBatch, DeploymentRun, Version, WorkflowRun, WorkflowStep, WorkflowStepRun
from app.services.build.worker import enqueue_build_batch
from app.services.deployment.worker import enqueue_deployment_run
from app.services.workflow.resolver import resolve_step_builders, resolve_step_manifests
from app.utils.error_logger import log_error

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


def _finish_step(run, step, success):
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
            return

    next_step = _next_step(step)
    if next_step is None:
        run.status = "completed_with_failures" if run.has_failed_step else "success"
        run.finished_at = datetime.utcnow()
        db.session.commit()
    else:
        db.session.commit()
        _start_step(run, next_step)


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
    _finish_step(run, step, success=False)


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
        batch = enqueue_build_batch(
            version=version,
            bump_type=step.bump_type,
            builder_branches=[(builder, builder.default_branch) for builder in builders],
            object_=step.object,
            additional_description=step.additional_description,
            requested_by=run.triggered_by,
            change_type_id=step.change_type_id,
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
    _finish_step(run, step, success)


def _tick(app):
    with app.app_context():
        for run in WorkflowRun.query.filter_by(status="queued").all():
            step = _first_step(run.workflow_id)
            if step is None:
                run.status = "success"
                run.started_at = datetime.utcnow()
                run.finished_at = datetime.utcnow()
                db.session.commit()
            else:
                _start_step(run, step)

        for run in WorkflowRun.query.filter_by(status="running").all():
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

    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    thread = threading.Thread(target=_poll_loop, args=(app,), daemon=True, name="workflow-orchestrator")
    thread.start()
