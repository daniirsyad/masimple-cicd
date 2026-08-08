from datetime import datetime, timedelta

from flask import render_template, url_for
from flask_login import current_user, login_required

from app.blueprints.main import main_bp
from app.models import (
    ActivityLog,
    Builder,
    BuildBatch,
    DeploymentManifest,
    DeploymentRun,
    DeploymentServer,
    ErrorLog,
    ImageBuild,
    Role,
    User,
    Version,
    VersionDocumentation,
)
from app.services.build.worker import get_engine_status
from app.services.deployment.worker import get_engine_status as get_deployment_engine_status

RECENT_BATCHES_LIMIT = 5
RECENT_ERRORS_DAYS = 7


def _module_stats():
    """Image Builder module stat cards — each gated behind the same
    permission its own list page requires, so the dashboard never surfaces a
    number for a resource the viewer couldn't otherwise see or count themselves.
    """
    stats = []
    if current_user.has_permission("builder.view"):
        stats.append(
            {"label": "Builders", "value": Builder.query.count(), "url": url_for("builders.index")}
        )
    if current_user.has_permission("version.view"):
        stats.append(
            {"label": "Versions", "value": Version.query.count(), "url": url_for("versions.list_versions")}
        )
    if current_user.has_permission("image.view"):
        stats.append(
            {
                "label": "Images Built",
                "value": ImageBuild.query.filter_by(status="success").count(),
                "url": url_for("images.list_images"),
            }
        )
    if current_user.has_permission("documentation.edit"):
        pending_count = (
            BuildBatch.query.join(VersionDocumentation)
            .filter(BuildBatch.status == "success", VersionDocumentation.change_type_id.is_(None))
            .count()
        )
        stats.append(
            {
                "label": "Documentation Pending",
                "value": pending_count,
                "url": url_for("documentation.list_documentation"),
            }
        )

    # Deployment module — same gate-behind-the-list-page's-own-permission
    # pattern as the Image Builder cards above.
    if current_user.has_permission("deployment_server.view"):
        stats.append(
            {"label": "Deployment Servers", "value": DeploymentServer.query.count(), "url": url_for("deployment_servers.index")}
        )
    if current_user.has_permission("deployment_manifest.view"):
        stats.append(
            {
                "label": "Deployment Manifests",
                "value": DeploymentManifest.query.count(),
                "url": url_for("deployment_manifests.index"),
            }
        )
    if current_user.has_permission("deployment_run.view"):
        stats.append(
            {"label": "Deployment Runs", "value": DeploymentRun.query.count(), "url": url_for("deployment_runs.index")}
        )
    return stats


@main_bp.route("/")
@login_required
def index():
    active_users_count = User.query.filter_by(is_active=True).count()
    roles_count = Role.query.count()
    recent_logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(5).all()

    module_stats = _module_stats()

    recent_batches = []
    if current_user.has_permission("image.view"):
        recent_batches = BuildBatch.query.order_by(BuildBatch.created_at.desc()).limit(RECENT_BATCHES_LIMIT).all()

    engine_status = None
    if current_user.has_permission("builder.view"):
        engine_status = get_engine_status()

    deployment_engine_status = None
    if current_user.has_permission("deployment_run.view"):
        deployment_engine_status = get_deployment_engine_status()

    recent_errors_count = None
    if current_user.has_permission("logs.view"):
        recent_errors_count = ErrorLog.query.filter(
            ErrorLog.created_at >= datetime.utcnow() - timedelta(days=RECENT_ERRORS_DAYS)
        ).count()

    return render_template(
        "main/index.html",
        active_users_count=active_users_count,
        roles_count=roles_count,
        recent_logs=recent_logs,
        module_stats=module_stats,
        recent_batches=recent_batches,
        engine_status=engine_status,
        deployment_engine_status=deployment_engine_status,
        recent_errors_count=recent_errors_count,
        recent_errors_days=RECENT_ERRORS_DAYS,
    )
