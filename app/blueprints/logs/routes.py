import uuid
from datetime import datetime, timedelta

from flask import render_template, request

from app.blueprints.logs import logs_bp
from app.models import ActivityLog, ErrorLog, User
from app.utils.decorators import permission_required

PER_PAGE = 20


@logs_bp.route("/")
@permission_required("logs.view")
def view_logs():
    query = ActivityLog.query

    user_id = request.args.get("user_id") or ""
    if user_id:
        try:
            query = query.filter(ActivityLog.user_id == uuid.UUID(user_id))
        except ValueError:
            user_id = ""

    action = request.args.get("action") or ""
    if action:
        query = query.filter(ActivityLog.action == action)

    date_from = request.args.get("date_from") or ""
    if date_from:
        try:
            query = query.filter(ActivityLog.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            date_from = ""

    date_to = request.args.get("date_to") or ""
    if date_to:
        try:
            query = query.filter(
                ActivityLog.created_at < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            )
        except ValueError:
            date_to = ""

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(ActivityLog.created_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False
    )

    users = User.query.order_by(User.username).all()
    actions = [
        row[0]
        for row in ActivityLog.query.with_entities(ActivityLog.action)
        .distinct()
        .order_by(ActivityLog.action)
        .all()
    ]

    return render_template(
        "logs/view.html",
        pagination=pagination,
        logs=pagination.items,
        users=users,
        actions=actions,
        selected_user_id=user_id,
        selected_action=action,
        date_from=date_from,
        date_to=date_to,
    )


@logs_bp.route("/errors")
@permission_required("logs.view")
def view_error_logs():
    query = ErrorLog.query

    source = request.args.get("source") or ""
    if source:
        query = query.filter(ErrorLog.source == source)

    date_from = request.args.get("date_from") or ""
    if date_from:
        try:
            query = query.filter(ErrorLog.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            date_from = ""

    date_to = request.args.get("date_to") or ""
    if date_to:
        try:
            query = query.filter(
                ErrorLog.created_at < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            )
        except ValueError:
            date_to = ""

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(ErrorLog.created_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False
    )

    sources = [
        row[0]
        for row in ErrorLog.query.with_entities(ErrorLog.source).distinct().order_by(ErrorLog.source).all()
    ]

    return render_template(
        "logs/errors.html",
        pagination=pagination,
        logs=pagination.items,
        sources=sources,
        selected_source=source,
        date_from=date_from,
        date_to=date_to,
    )
