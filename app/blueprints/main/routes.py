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
from app.services.workflow.worker import awaiting_review_step_runs_for

RECENT_BATCHES_LIMIT = 5
RECENT_ERRORS_DAYS = 7

# Decorative only (stat-figure icons on the dashboard's stat cards) — Feather
# icon paths, stroke-based to match the sidebar's own non-mdi icons (see
# seeds/seed_menu.py's _FILE_TEXT_ICON etc.). Kept as their own small
# constants here rather than duplicated inline per stat below.
_ICON_USERS = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>'
_ICON_SHIELD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>'
_ICON_BOX = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>'
_ICON_TAG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41L11 3.83V3H3v8h.83l9.58 9.59a2 2 0 0 0 2.83 0l4.35-4.35a2 2 0 0 0 0-2.83z"></path><line x1="7" y1="7" x2="7.01" y2="7"></line></svg>'
_ICON_IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>'
_ICON_FILE_TEXT = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>'
_ICON_SERVER = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line></svg>'
_ICON_LAYERS = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>'
_ICON_ACTIVITY = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>'


def _module_stats():
    """Image Builder module stat cards — each gated behind the same
    permission its own list page requires, so the dashboard never surfaces a
    number for a resource the viewer couldn't otherwise see or count themselves.
    """
    stats = []
    if current_user.has_permission("builder.view"):
        stats.append(
            {
                "label": "Builders",
                "value": Builder.query.count(),
                "url": url_for("builders.index"),
                "icon": _ICON_BOX,
            }
        )
    if current_user.has_permission("version.view"):
        stats.append(
            {
                "label": "Versions",
                "value": Version.query.count(),
                "url": url_for("versions.list_versions"),
                "icon": _ICON_TAG,
            }
        )
    if current_user.has_permission("image.view"):
        stats.append(
            {
                "label": "Images Built",
                "value": ImageBuild.query.filter_by(status="success").count(),
                "url": url_for("images.list_images"),
                "icon": _ICON_IMAGE,
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
                "icon": _ICON_FILE_TEXT,
            }
        )

    # Deployment module — same gate-behind-the-list-page's-own-permission
    # pattern as the Image Builder cards above.
    if current_user.has_permission("deployment_server.view"):
        stats.append(
            {
                "label": "Deployment Servers",
                "value": DeploymentServer.query.count(),
                "url": url_for("deployment_servers.index"),
                "icon": _ICON_SERVER,
            }
        )
    if current_user.has_permission("deployment_manifest.view"):
        stats.append(
            {
                "label": "Deployment Manifests",
                "value": DeploymentManifest.query.count(),
                "url": url_for("deployment_manifests.index"),
                "icon": _ICON_LAYERS,
            }
        )
    if current_user.has_permission("deployment_run.view"):
        stats.append(
            {
                "label": "Deployment Runs",
                "value": DeploymentRun.query.count(),
                "url": url_for("deployment_runs.index"),
                "icon": _ICON_ACTIVITY,
            }
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

    awaiting_review_step_runs = awaiting_review_step_runs_for(current_user)

    return render_template(
        "main/index.html",
        awaiting_review_step_runs=awaiting_review_step_runs,
        active_users_count=active_users_count,
        active_users_icon=_ICON_USERS,
        roles_count=roles_count,
        roles_icon=_ICON_SHIELD,
        recent_logs=recent_logs,
        module_stats=module_stats,
        recent_batches=recent_batches,
        engine_status=engine_status,
        deployment_engine_status=deployment_engine_status,
        recent_errors_count=recent_errors_count,
        recent_errors_days=RECENT_ERRORS_DAYS,
    )
