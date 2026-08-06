import uuid

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.security import generate_password_hash

from app.blueprints.users import users_bp
from app.blueprints.users.forms import CreateUserForm, EditUserForm
from app.extensions import db
from app.models import ActivityLog, Role, User
from app.utils.decorators import permission_required
from app.utils.logger import log_activity


def _role_choices():
    return [(str(role.id), role.name) for role in Role.query.order_by(Role.name).all()]


def _edit_prefix(user_id):
    return f"{user_id}-"


def _render_users_list(create_form=None, open_modal=None, invalid_edit=None):
    query = User.query

    search = request.args.get("q", "").strip()
    if search:
        query = query.filter(User.username.ilike(f"%{search}%"))

    role_id = request.args.get("role_id") or ""
    if role_id:
        query = query.filter(User.role_id == uuid.UUID(role_id))

    status = request.args.get("status") or ""
    if status == "active":
        query = query.filter(User.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(User.is_active.is_(False))

    users = query.order_by(User.username).all()
    roles = Role.query.order_by(Role.name).all()

    if create_form is None:
        create_form = CreateUserForm()
    create_form.role_id.choices = _role_choices()

    invalid_user_id, invalid_form = invalid_edit or (None, None)

    edit_forms = {}
    for user in users:
        if user.id == invalid_user_id:
            edit_forms[user.id] = invalid_form
        else:
            form = EditUserForm(obj=user, prefix=_edit_prefix(user.id))
            form.role_id.choices = _role_choices()
            form.role_id.data = str(user.role_id) if user.role_id else None
            edit_forms[user.id] = form

    return render_template(
        "users/list.html",
        users=users,
        roles=roles,
        search=search,
        selected_role_id=role_id,
        selected_status=status,
        create_form=create_form,
        edit_forms=edit_forms,
        open_modal=open_modal,
    )


@users_bp.route("/")
@permission_required("user.view")
def list_users():
    return _render_users_list()


@users_bp.route("/create", methods=["POST"])
@permission_required("user.create")
def create_user():
    form = CreateUserForm()
    form.role_id.choices = _role_choices()

    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            password_hash=generate_password_hash(form.password.data),
            full_name=form.full_name.data or None,
            role_id=uuid.UUID(form.role_id.data),
            created_by=current_user.id,
        )
        db.session.add(user)
        db.session.commit()

        log_activity(
            action="CREATE_USER",
            target_type="user",
            target_id=str(user.id),
            description=f"Created user '{user.username}'",
        )

        flash(f"User '{user.username}' created.", "success")
        return redirect(url_for("users.list_users"))

    return _render_users_list(create_form=form, open_modal="create-modal")


@users_bp.route("/<uuid:user_id>/edit", methods=["POST"])
@permission_required("user.edit")
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    form = EditUserForm(prefix=_edit_prefix(user_id))
    form.role_id.choices = _role_choices()

    if form.validate_on_submit():
        user.full_name = form.full_name.data or None
        user.role_id = uuid.UUID(form.role_id.data)
        user.is_active = form.is_active.data

        if form.new_password.data:
            user.password_hash = generate_password_hash(form.new_password.data)

        db.session.commit()

        log_activity(
            action="UPDATE_USER",
            target_type="user",
            target_id=str(user.id),
            description=f"Updated user '{user.username}'",
        )

        flash(f"User '{user.username}' updated.", "success")
        return redirect(url_for("users.list_users"))

    return _render_users_list(open_modal=f"edit-modal-{user_id}", invalid_edit=(user_id, form))


@users_bp.route("/<uuid:user_id>/toggle-active", methods=["POST"])
@permission_required("user.delete")
def toggle_active(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = not user.is_active
    db.session.commit()

    action = "DEACTIVATE_USER" if not user.is_active else "REACTIVATE_USER"
    log_activity(
        action=action,
        target_type="user",
        target_id=str(user.id),
        description=f"{'Deactivated' if not user.is_active else 'Reactivated'} user '{user.username}'",
    )

    flash(f"User '{user.username}' is now {'active' if user.is_active else 'inactive'}.", "success")
    return redirect(url_for("users.list_users"))


@users_bp.route("/<uuid:user_id>/delete", methods=["POST"])
@permission_required("user.delete")
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("users.list_users"))

    username = user.username
    user_id_str = str(user.id)

    # Detach this user from history/records instead of cascading the delete,
    # so activity log rows and other users' "created by" attribution survive.
    ActivityLog.query.filter_by(user_id=user.id).update({"user_id": None})
    User.query.filter_by(created_by=user.id).update({"created_by": None})

    db.session.delete(user)
    db.session.commit()

    log_activity(
        action="DELETE_USER",
        target_type="user",
        target_id=user_id_str,
        description=f"Deleted user '{username}'",
    )

    flash(f"User '{username}' deleted.", "success")
    return redirect(url_for("users.list_users"))
