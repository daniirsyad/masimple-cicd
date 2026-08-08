import uuid
from datetime import datetime, timedelta

from flask import jsonify, render_template, request

from app.blueprints.deployment_runs import deployment_runs_bp
from app.models import DeploymentExecution, DeploymentManifest, DeploymentRun, DeploymentServer
from app.services.deployment.worker import get_engine_status, get_run_progress
from app.utils.decorators import permission_required

PER_PAGE = 20
LOG_TAIL_LINES = 100

RUN_STATUS_CHOICES = ("queued", "running", "success", "partial_failure", "failed")


def _parse_uuid(value):
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _log_tail(log_text, max_lines=LOG_TAIL_LINES):
    if not log_text:
        return ""
    return "\n".join(log_text.splitlines()[-max_lines:])


def _run_display_name(run, executions):
    """run.group_name for a group run; for a standalone run (group_name is
    null), "<manifest name> (standalone)" instead of the previous bare
    "Standalone deploy" — which manifest actually ran was otherwise only
    visible by opening the run. Falls back to "Standalone deploy" only if
    the run somehow has no executions at all (nothing to name).
    """
    if run.group_name:
        return run.group_name

    names = []
    seen = set()
    for execution in executions:
        name = execution.manifest.name if execution.manifest else "(deleted manifest)"
        if name not in seen:
            seen.add(name)
            names.append(name)

    if not names:
        return "Standalone deploy"
    return f"{', '.join(names)} (standalone)"


@deployment_runs_bp.route("/")
@permission_required("deployment_run.view")
def index():
    query = DeploymentRun.query

    status = request.args.get("status") or ""
    if status:
        query = query.filter(DeploymentRun.status == status)

    manifest_id = _parse_uuid(request.args.get("manifest_id"))
    server_id = _parse_uuid(request.args.get("server_id"))
    if manifest_id or server_id:
        matching_run_ids = DeploymentExecution.query.with_entities(DeploymentExecution.run_id)
        if manifest_id:
            matching_run_ids = matching_run_ids.filter(DeploymentExecution.manifest_id == manifest_id)
        if server_id:
            matching_run_ids = matching_run_ids.filter(DeploymentExecution.server_id == server_id)
        query = query.filter(DeploymentRun.id.in_(matching_run_ids))

    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    parsed_from = _parse_date(date_from)
    if parsed_from:
        query = query.filter(DeploymentRun.created_at >= parsed_from)
    parsed_to = _parse_date(date_to)
    if parsed_to:
        query = query.filter(DeploymentRun.created_at < parsed_to + timedelta(days=1))

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(DeploymentRun.created_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False
    )

    progress_by_run = {run.id: get_run_progress(run.id) for run in pagination.items}

    run_ids = [run.id for run in pagination.items]
    executions_by_run = {}
    if run_ids:
        for execution in DeploymentExecution.query.filter(DeploymentExecution.run_id.in_(run_ids)).all():
            executions_by_run.setdefault(execution.run_id, []).append(execution)
    run_names = {run.id: _run_display_name(run, executions_by_run.get(run.id, [])) for run in pagination.items}

    return render_template(
        "deployment_runs/list.html",
        pagination=pagination,
        runs=pagination.items,
        selected_status=status,
        status_choices=RUN_STATUS_CHOICES,
        manifests=DeploymentManifest.query.order_by(DeploymentManifest.name).all(),
        servers=DeploymentServer.query.order_by(DeploymentServer.name).all(),
        selected_manifest_id=request.args.get("manifest_id") or "",
        selected_server_id=request.args.get("server_id") or "",
        date_from=date_from,
        date_to=date_to,
        progress_by_run=progress_by_run,
        run_names=run_names,
    )


@deployment_runs_bp.route("/<uuid:run_id>")
@permission_required("deployment_run.view")
def detail(run_id):
    run = DeploymentRun.query.get_or_404(run_id)
    executions = (
        DeploymentExecution.query.filter_by(run_id=run.id).order_by(DeploymentExecution.created_at.asc()).all()
    )
    return render_template(
        "deployment_runs/detail.html",
        run=run,
        executions=executions,
        log_tails={e.id: _log_tail(e.log) for e in executions},
        run_display_name=_run_display_name(run, executions),
    )


@deployment_runs_bp.route("/status")
@permission_required("deployment_run.view")
def status():
    engine_status = get_engine_status()
    running = engine_status["running"]

    running_payload = None
    if running is not None:
        running_payload = {
            "id": str(running.id),
            "manifest": running.manifest.name if running.manifest else "(deleted manifest)",
            "server": running.server.name,
            "run_id": str(running.run_id),
            "log_tail": _log_tail(running.log),
            "progress": get_run_progress(running.run_id),
        }

    queue = [
        {
            "id": str(execution.id),
            "manifest": execution.manifest.name if execution.manifest else "(deleted manifest)",
            "server": execution.server.name,
            "run_id": str(execution.run_id),
            "position": index + 1,
        }
        for index, execution in enumerate(engine_status["queued"])
    ]

    return jsonify({"busy": engine_status["busy"], "running": running_payload, "queue": queue})
