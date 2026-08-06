from flask import render_template
from flask_login import login_required

from app.blueprints.main import main_bp
from app.models import ActivityLog, Role, User


@main_bp.route("/")
@login_required
def index():
    active_users_count = User.query.filter_by(is_active=True).count()
    roles_count = Role.query.count()
    recent_logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(5).all()

    return render_template(
        "main/index.html",
        active_users_count=active_users_count,
        roles_count=roles_count,
        recent_logs=recent_logs,
    )
