import uuid

from flask import flash, redirect, render_template, request, url_for

from app.blueprints.roles import roles_bp
from app.blueprints.roles.forms import RoleForm
from app.extensions import db
from app.models import Permission, Role
from app.utils.decorators import permission_required
from app.utils.logger import log_activity


def _permission_choices():
    return [(str(permission.id), permission.code) for permission in Permission.query.order_by(Permission.code).all()]


def _edit_prefix(role_id):
    return f"{role_id}-"


def _render_roles_list(create_form=None, open_modal=None, invalid_edit=None):
    roles = Role.query.order_by(Role.name).all()

    if create_form is None:
        create_form = RoleForm()
    create_form.permissions.choices = _permission_choices()

    invalid_role_id, invalid_form = invalid_edit or (None, None)

    edit_forms = {}
    for role in roles:
        if role.id == invalid_role_id:
            edit_forms[role.id] = invalid_form
        else:
            form = RoleForm(obj=role, prefix=_edit_prefix(role.id))
            form.permissions.choices = _permission_choices()
            form.permissions.data = [str(permission.id) for permission in role.permissions]
            edit_forms[role.id] = form

    return render_template(
        "roles/list.html",
        roles=roles,
        create_form=create_form,
        edit_forms=edit_forms,
        open_modal=open_modal,
    )


@roles_bp.route("/")
@permission_required("role.view")
def list_roles():
    return _render_roles_list()


@roles_bp.route("/create", methods=["POST"])
@permission_required("role.create")
def create_role():
    form = RoleForm()
    form.permissions.choices = _permission_choices()

    if form.validate_on_submit():
        if Role.query.filter_by(name=form.name.data).first():
            flash("Role name already exists.", "error")
            return _render_roles_list(create_form=form, open_modal="create-modal")

        role = Role(name=form.name.data, description=form.description.data or None)
        selected_ids = {uuid.UUID(pid) for pid in form.permissions.data}
        role.permissions = Permission.query.filter(Permission.id.in_(selected_ids)).all()

        db.session.add(role)
        db.session.commit()

        log_activity(
            action="CREATE_ROLE",
            target_type="role",
            target_id=str(role.id),
            description=f"Created role '{role.name}'",
        )

        flash(f"Role '{role.name}' created.", "success")
        return redirect(url_for("roles.list_roles"))

    return _render_roles_list(create_form=form, open_modal="create-modal")


@roles_bp.route("/<uuid:role_id>/edit", methods=["POST"])
@permission_required("role.edit")
def edit_role(role_id):
    role = Role.query.get_or_404(role_id)
    form = RoleForm(prefix=_edit_prefix(role_id))
    form.permissions.choices = _permission_choices()

    if form.validate_on_submit():
        duplicate = Role.query.filter(Role.name == form.name.data, Role.id != role.id).first()
        if duplicate:
            flash("Role name already exists.", "error")
            return _render_roles_list(open_modal=f"edit-modal-{role_id}", invalid_edit=(role_id, form))

        role.name = form.name.data
        role.description = form.description.data or None
        selected_ids = {uuid.UUID(pid) for pid in form.permissions.data}
        role.permissions = Permission.query.filter(Permission.id.in_(selected_ids)).all()

        db.session.commit()

        log_activity(
            action="UPDATE_ROLE",
            target_type="role",
            target_id=str(role.id),
            description=f"Updated role '{role.name}'",
        )

        flash(f"Role '{role.name}' updated.", "success")
        return redirect(url_for("roles.list_roles"))

    return _render_roles_list(open_modal=f"edit-modal-{role_id}", invalid_edit=(role_id, form))


@roles_bp.route("/<uuid:role_id>/delete", methods=["POST"])
@permission_required("role.delete")
def delete_role(role_id):
    role = Role.query.get_or_404(role_id)

    if role.is_system:
        flash(f"Role '{role.name}' is a built-in system role and cannot be deleted.", "error")
        return redirect(url_for("roles.list_roles"))

    if role.users:
        flash(
            f"Role '{role.name}' is still assigned to {len(role.users)} user(s) and cannot be deleted.",
            "error",
        )
        return redirect(url_for("roles.list_roles"))

    role_name = role.name
    role_id_str = str(role.id)
    db.session.delete(role)
    db.session.commit()

    log_activity(
        action="DELETE_ROLE",
        target_type="role",
        target_id=role_id_str,
        description=f"Deleted role '{role_name}'",
    )

    flash(f"Role '{role_name}' deleted.", "success")
    return redirect(url_for("roles.list_roles"))
