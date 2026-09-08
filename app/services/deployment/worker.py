"""Deploy worker — an independent poll thread/queue from
app/services/build/worker.py's build worker. A deploy and a build may run
concurrently (they're different resources: kubectl/agent-API calls vs docker
builds); this module enforces its own, separate "only one deployment
execution runs at a time system-wide" via DeploymentExecution's own partial
unique index (ix_deployment_executions_single_running), not the build
worker's ImageBuild one.
"""
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import DeploymentExecution, DeploymentRun
from app.services.deployment.helpers import provider_for_server
from app.services.deployment.resolver import UnresolvedPlaceholderError, resolve_manifest
from app.services.discord.helpers import notify_deploy_finished as discord_notify_deploy_finished
from app.services.discord.helpers import notify_deploy_started as discord_notify_deploy_started
from app.services.telegram.helpers import notify_deploy_finished, notify_deploy_started
from app.utils.error_logger import log_error
from app.utils.runtime import is_werkzeug_reloader_parent
from app.utils.system_config import get_system_config

POLL_INTERVAL_SECONDS = 2
# Floor for the admin-configurable deployment_status_check_interval_seconds
# — protects the poller (and every registered server's API/kubectl) from a
# runaway tight loop if someone sets it to 0 or a negative value.
STATUS_POLL_MIN_INTERVAL_SECONDS = 5

# Heartbeat cadence for detecting a worker process that died mid-deploy —
# mirrors app/services/build/worker.py's own heartbeat constants/reasoning
# exactly (kept independent, not shared, same as the rest of this module's
# duplication of the build worker's queue pattern).
HEARTBEAT_INTERVAL_SECONDS = 15
HEARTBEAT_STALE_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 4

_worker_started = False
_worker_lock = threading.Lock()
_poller_started = False
_poller_lock = threading.Lock()

# Which DeploymentExecution id (if any) *this process* currently owns — at
# most one of the fleet's processes will ever have this set, since only one
# DeploymentExecution can be 'running' system-wide
# (ix_deployment_executions_single_running).
_current_execution_id = None
_current_execution_lock = threading.Lock()


def get_current_deployment(manifest_id, server_id):
    """The most recent *successful* execution for this (manifest, server)
    pair, ignoring failed/skipped/in-flight attempts — those never actually
    changed what's on the cluster, so they shouldn't count as "current".
    None if this pair has never had a successful apply or stop.
    """
    return (
        DeploymentExecution.query.join(DeploymentRun, DeploymentExecution.run_id == DeploymentRun.id)
        .filter(
            DeploymentExecution.manifest_id == manifest_id,
            DeploymentExecution.server_id == server_id,
            DeploymentExecution.status == "success",
        )
        .order_by(DeploymentExecution.created_at.desc())
        .first()
    )


def is_currently_deployed(manifest_id, server_id):
    """True iff this (manifest, server) pair's most recent successful
    execution wasn't a "stop" — i.e. deployed, updated (still a "deploy"
    action), or restarted all count as still deployed; only a successful
    "stop" ever undeploys. NOT the same as "action == deploy" — a
    successful restart's own execution becomes the new "most recent", and
    a restart doesn't undeploy anything.
    """
    execution = get_current_deployment(manifest_id, server_id)
    return execution is not None and execution.run.action != "stop"


def get_available_update(manifest, server):
    """None if there's nothing to update (not currently deployed on this
    server, the manifest can't currently be resolved, or it's already on
    the latest resolvable version) — otherwise the version string it would
    update to. Powers the manifest table's "Update" button/badge: Deploy
    hides once fully live, so this is the only way to notice a newer
    Builder image landed after a manifest was already deployed everywhere.
    """
    current = get_current_deployment(manifest.id, server.id)
    if current is None or current.run.action == "stop":
        return None

    try:
        _rendered_yaml, resolved_versions = resolve_manifest(manifest)
    except UnresolvedPlaceholderError:
        return None

    target_version_string = "; ".join(f"{key}={tag}" for key, tag in resolved_versions.items()) or None
    if target_version_string and target_version_string != current.resolved_version_string:
        return target_version_string
    return None


def enqueue_deployment_run(manifests, triggered_by, group_name=None, action="deploy"):
    """Creates one DeploymentRun + one DeploymentExecution per (manifest,
    target server) pair — mirrors enqueue_build_batch.

    action="deploy" (default, also used for "Update" — an update is just a
    fresh deploy that happens to re-resolve to a newer version): applies
    every manifest to every server on its own `target_servers` (chosen at
    manifest setup time), unchanged from before.

    action="stop" or "restart": act only on (manifest, server) pairs that
    are actually currently deployed — each execution's source_execution_id
    points at the current deployment (deploy/update/restart, whichever was
    most recent) being torn down or restarted. A "stop" acts on exactly
    what was applied, via source_execution_id's rendered_yaml, without
    re-resolving; a "restart" only uses source_execution_id to confirm
    something is actually live, then re-resolves the manifest fresh (see
    _run_deployment) so it picks up any since-changed values (e.g. an
    edited Secret) rather than blindly replaying stale content. Pairs with
    nothing currently live are silently skipped.

    Either way, `manifests` must already be in the order the caller wants
    executions created in (ascending manifest.order for a group deploy,
    descending for a group stop/restart — see deployment_manifests.routes),
    since the worker claims queued executions oldest-created-first.
    """
    run = DeploymentRun(group_name=group_name, triggered_by=triggered_by, status="queued", action=action)
    db.session.add(run)
    db.session.flush()  # assign run.id so the executions below can reference it

    execution_count = 0
    for manifest in manifests:
        for server in manifest.target_servers:
            if action == "deploy":
                db.session.add(
                    DeploymentExecution(run_id=run.id, manifest_id=manifest.id, server_id=server.id, status="queued")
                )
                execution_count += 1
            else:
                source = get_current_deployment(manifest.id, server.id)
                if source is None or source.run.action == "stop":
                    continue
                db.session.add(
                    DeploymentExecution(
                        run_id=run.id,
                        manifest_id=manifest.id,
                        server_id=server.id,
                        source_execution_id=source.id,
                        status="queued",
                    )
                )
                execution_count += 1

    db.session.commit()
    return run, execution_count


def get_engine_status():
    """Snapshot of what the deploy worker is doing right now — mirrors
    build.worker.get_engine_status(), for /deployment-runs's polling.
    """
    running = DeploymentExecution.query.filter_by(status="running").first()
    queued = (
        DeploymentExecution.query.filter_by(status="queued")
        .order_by(DeploymentExecution.created_at.asc())
        .all()
    )
    return {"busy": running is not None, "running": running, "queued": queued}


def get_run_progress(run_id):
    """'X of Y done' — a run-level aggregate over its DeploymentExecutions'
    statuses, mirrors build.worker.get_batch_progress.
    """
    executions = DeploymentExecution.query.filter_by(run_id=run_id).all()
    total = len(executions)
    finished = sum(1 for execution in executions if execution.status in ("success", "failed", "skipped"))
    succeeded = sum(1 for execution in executions if execution.status == "success")
    return {"total": total, "finished": finished, "succeeded": succeeded}


def _claim_next_job():
    """Atomically claim the oldest queued DeploymentExecution, enforcing "only
    one deployment runs at a time" globally across every process — see the
    module docstring for why this is a separate queue/index from the build
    worker's. Returns the claimed execution's id, or None if there was
    nothing to claim or this attempt lost the race to another already-
    running execution (see build.worker._claim_next_job's docstring for the
    full SKIP LOCKED + partial-unique-index race reasoning, identical here).
    """
    execution = (
        DeploymentExecution.query.filter_by(status="queued")
        .order_by(DeploymentExecution.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if execution is None:
        db.session.rollback()
        return None

    execution.status = "running"
    execution.started_at = datetime.utcnow()
    execution.heartbeat_at = execution.started_at

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None

    # DeploymentRun has no started_at of its own (unlike BuildBatch's
    # full_version_string flag) to check "is this the run's first claimed
    # execution" — count is equivalent: only this claim's own execution can
    # have left "queued" so far within this run iff it's the first.
    started_count = DeploymentExecution.query.filter(
        DeploymentExecution.run_id == execution.run_id, DeploymentExecution.status != "queued"
    ).count()
    if started_count == 1:
        notify_deploy_started(execution.run)
        discord_notify_deploy_started(execution.run)
    return execution.id


def _update_run_status(run_id):
    """Aggregates a DeploymentRun's status from its DeploymentExecutions.

    Differs from build.worker._update_batch_status ("continue, report
    partial_failure" for every execution regardless of outcome): a group
    deploy aborts remaining work on first failure — once any execution in a
    run has failed, every execution still "queued" in that same run is
    marked "skipped" instead of ever being claimed by _claim_next_job. An
    execution already "running" when the failure happens is left to finish
    naturally (there's no way to safely kill an in-flight kubectl/API call
    from here) — its own outcome still feeds into this same aggregation
    once it completes.
    """
    run = DeploymentRun.query.get(run_id)
    if run is None:
        return

    TERMINAL_STATUSES = {"success", "failed", "partial_failure"}
    was_terminal = run.status in TERMINAL_STATUSES

    executions = DeploymentExecution.query.filter_by(run_id=run_id).all()
    statuses = {execution.status for execution in executions}

    if "failed" in statuses:
        for execution in executions:
            if execution.status == "queued":
                execution.status = "skipped"
        statuses = {execution.status for execution in executions}

    if "running" in statuses:
        run.status = "running"
    elif "failed" in statuses:
        run.status = "partial_failure" if "success" in statuses else "failed"
    elif statuses == {"success"}:
        run.status = "success"
    else:
        run.status = "queued"

    db.session.commit()

    if not was_terminal and run.status in TERMINAL_STATUSES:
        notify_deploy_finished(run)
        discord_notify_deploy_finished(run)


def _heartbeat_tick(app):
    """Bump heartbeat_at on the DeploymentExecution this process currently
    owns, if any — mirrors build.worker._heartbeat_tick exactly.
    """
    with _current_execution_lock:
        execution_id = _current_execution_id
    if execution_id is None:
        return

    with app.app_context():
        DeploymentExecution.query.filter_by(id=execution_id, status="running").update(
            {"heartbeat_at": datetime.utcnow()}, synchronize_session=False
        )
        db.session.commit()
        db.session.remove()


def _heartbeat_loop(app):
    while True:
        try:
            _heartbeat_tick(app)
        except Exception as exc:  # a bad tick must not kill the heartbeat thread
            with app.app_context():
                log_error(
                    source="deployment.worker.heartbeat",
                    exc=exc,
                    description=f"Deploy worker heartbeat tick failed: {exc}",
                )
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def _reap_stale_running_job():
    """If the single 'running' DeploymentExecution's heartbeat has gone
    stale (or was never set — a pre-migration ghost row), the process that
    claimed it is assumed to have died mid-deploy. Fails it out so the
    single-flight slot (ix_deployment_executions_single_running) frees up
    for the next queued execution — see build.worker._reap_stale_running_job
    for the full reasoning behind the WHERE-guarded-UPDATE-plus-rowcount
    idiom used here, identical to that one.
    """
    stale = DeploymentExecution.query.filter_by(status="running").first()
    if stale is None:
        db.session.rollback()
        return

    threshold = datetime.utcnow() - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
    if stale.heartbeat_at is not None and stale.heartbeat_at >= threshold:
        db.session.rollback()
        return

    rowcount = (
        DeploymentExecution.query.filter(
            DeploymentExecution.id == stale.id,
            DeploymentExecution.status == "running",
        )
        .filter(db.or_(DeploymentExecution.heartbeat_at.is_(None), DeploymentExecution.heartbeat_at < threshold))
        .update({"status": "failed", "finished_at": datetime.utcnow()}, synchronize_session=False)
    )
    if rowcount == 0:
        db.session.rollback()
        return

    db.session.commit()

    execution = DeploymentExecution.query.get(stale.id)
    execution.log = (execution.log or "") + (
        f"\n\n[reaper] No heartbeat for over {HEARTBEAT_STALE_SECONDS}s — assuming the "
        "worker process that claimed this execution crashed. Marking failed.\n"
    )
    db.session.commit()
    log_error(
        source="deployment.worker.reap_stale_job",
        description=f"Reaped stale running execution {execution.id} (run {execution.run_id}): no heartbeat.",
    )
    _update_run_status(execution.run_id)


def _run_deployment(app, execution_id):
    """Runs one claimed DeploymentExecution: resolve -> apply for a "deploy"
    run, resolve -> delete+apply for a "restart" run, or a straight delete of
    the source execution's rendered_yaml for a "stop" run (see
    enqueue_deployment_run).

    Always wrapped in try/except so one bad execution (unresolved
    placeholder, unreachable server, a rejected manifest, ...) marks that
    one DeploymentExecution as failed instead of killing the worker thread —
    the poll loop must keep running afterward.
    """
    with app.app_context():
        execution = DeploymentExecution.query.get(execution_id)
        if execution is None:
            return

        log_lines = []

        def flush_log():
            execution.log = "".join(log_lines)
            db.session.commit()

        try:
            manifest = execution.manifest
            server = execution.server
            run_action = execution.run.action  # "deploy" | "stop" | "restart"

            if run_action == "stop":
                source = execution.source_execution
                if source is None or not source.rendered_yaml:
                    raise RuntimeError("No applied manifest recorded for this execution to stop.")

                verb = "Stop"
                log_lines.append(f"Stopping '{manifest.name}' on server '{server.name}'...\n")
                flush_log()

                result = provider_for_server(server).delete(source.rendered_yaml)
            elif run_action == "restart":
                # Re-resolve from scratch, same as a "deploy" — a restart
                # should pick up whatever the manifest's current template/
                # secret values are, not blindly replay whatever was last
                # applied (that silently reapplied stale Secret data even
                # after it had been edited).
                verb = "Restart"
                log_lines.append(f"Resolving version placeholders for '{manifest.name}'...\n")
                flush_log()

                rendered_yaml, resolved_versions = resolve_manifest(manifest)
                execution.rendered_yaml = rendered_yaml
                execution.resolved_version_string = (
                    "; ".join(f"{key}={tag}" for key, tag in resolved_versions.items()) or None
                )

                log_lines.append(f"Restarting '{manifest.name}' on server '{server.name}'...\n")
                flush_log()
                result = provider_for_server(server).restart(
                    rendered_yaml, wait_timeout_seconds=manifest.wait_for_ready_timeout_seconds
                )
            else:
                # Resolve placeholders before ever building a provider — an
                # unresolvable {{SYS:VERSION[:key]}} is a manifest-authoring
                # mistake that should surface as such, not get masked by
                # whatever the target server's credentials happen to be.
                verb = "Deploy"
                log_lines.append(f"Resolving version placeholders for '{manifest.name}'...\n")
                flush_log()

                rendered_yaml, resolved_versions = resolve_manifest(manifest)
                execution.rendered_yaml = rendered_yaml
                execution.resolved_version_string = (
                    "; ".join(f"{key}={tag}" for key, tag in resolved_versions.items()) or None
                )

                log_lines.append(f"Applying to server '{server.name}'...\n")
                flush_log()
                result = provider_for_server(server).apply(
                    rendered_yaml, wait_timeout_seconds=manifest.wait_for_ready_timeout_seconds
                )

            log_lines.append(result.log or "")

            if not result.success:
                execution.status = "failed"
                log_lines.append(f"\n{verb} failed: {result.error}\n")
                log_error(
                    source="deployment.worker.run_deployment",
                    description=f"Execution {execution.id} (run {execution.run_id}) failed: {result.error}",
                    detail=result.log,
                )
            else:
                execution.status = "success"
                log_lines.append(f"\n{verb} succeeded.\n")

        except Exception as exc:  # includes UnresolvedPlaceholderError — a bad deploy must not kill the worker thread
            execution.status = "failed"
            log_lines.append(f"\nERROR: {exc}\n")
            log_error(
                source="deployment.worker.run_deployment",
                exc=exc,
                description=f"Execution {execution.id} (run {execution.run_id}) failed: {exc}",
            )
        finally:
            flush_log()
            execution.finished_at = datetime.utcnow()
            db.session.commit()
            _update_run_status(execution.run_id)
            db.session.remove()


def _poll_loop(app):
    global _current_execution_id
    while True:
        try:
            with app.app_context():
                _reap_stale_running_job()
                execution_id = _claim_next_job()
                db.session.remove()
        except Exception as exc:
            # Same reasoning as app/services/build/worker.py's _poll_loop: a
            # bad tick here must not kill this thread, or the entire deploy
            # pipeline silently stops claiming queued deployments forever.
            with app.app_context():
                db.session.rollback()
                db.session.remove()
                log_error(
                    source="deployment.worker.poll_loop",
                    exc=exc,
                    description=f"Deploy worker poll loop iteration failed: {exc}",
                )
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if execution_id is not None:
            with _current_execution_lock:
                _current_execution_id = execution_id
            try:
                _run_deployment(app, execution_id)
            finally:
                with _current_execution_lock:
                    _current_execution_id = None
        else:
            time.sleep(POLL_INTERVAL_SECONDS)


def _live_check_candidates():
    """Every DeploymentExecution that is its (manifest, server) pair's
    current deployment — i.e. actually live and worth re-checking. Matches
    is_currently_deployed's definition: anything but a successful "stop".
    """
    pairs = (
        db.session.query(DeploymentExecution.manifest_id, DeploymentExecution.server_id)
        .filter(DeploymentExecution.status == "success")
        .distinct()
        .all()
    )
    candidates = []
    for manifest_id, server_id in pairs:
        execution = get_current_deployment(manifest_id, server_id)
        if execution is not None and execution.run.action != "stop":
            candidates.append(execution)
    return candidates


def _refresh_live_status(app):
    with app.app_context():
        for execution in _live_check_candidates():
            try:
                provider = provider_for_server(execution.server)
                status = provider.get_live_status(execution.rendered_yaml)
            except NotImplementedError:
                # This provider type (e.g. "api") has no reliable way to
                # tell — leave live_status as whatever it already was
                # rather than guessing.
                continue
            except Exception as exc:
                log_error(
                    source="deployment.worker.status_poll",
                    exc=exc,
                    description=f"Live-status check failed for execution {execution.id}: {exc}",
                )
                continue

            execution.live_status = status
            execution.live_checked_at = datetime.utcnow()
            db.session.commit()
        db.session.remove()


def _status_poll_loop(app):
    while True:
        try:
            _refresh_live_status(app)
        except Exception as exc:
            with app.app_context():
                log_error(
                    source="deployment.worker.status_poll",
                    exc=exc,
                    description="Live-status poll loop iteration failed.",
                )

        with app.app_context():
            interval = get_system_config().deployment_status_check_interval_seconds
        time.sleep(max(interval, STATUS_POLL_MIN_INTERVAL_SECONDS))


def start_worker(app):
    """Start this process's background deploy-worker thread, once.

    Same TESTING-skip and Werkzeug-reloader guards as
    app/services/build/worker.py's start_worker — see there for why.
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

    thread = threading.Thread(target=_poll_loop, args=(app,), daemon=True, name="deployment-worker")
    thread.start()

    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(app,), daemon=True, name="deployment-worker-heartbeat"
    )
    heartbeat_thread.start()


def start_status_poller(app):
    """Start this process's background live-status-poller thread, once.

    Independent of start_worker's deploy queue — read-only `kubectl get`
    checks don't need to compete for the single-flight deploy slot. Same
    TESTING-skip / Werkzeug-reloader guards as start_worker.
    """
    global _poller_started

    if app.config.get("TESTING"):
        return

    if is_werkzeug_reloader_parent(app):
        return

    with _poller_lock:
        if _poller_started:
            return
        _poller_started = True

    thread = threading.Thread(target=_status_poll_loop, args=(app,), daemon=True, name="deployment-status-poller")
    thread.start()
