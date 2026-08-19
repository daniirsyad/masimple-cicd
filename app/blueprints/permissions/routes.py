from flask import flash, redirect, render_template, url_for

from app.blueprints.permissions import permissions_bp
from app.blueprints.permissions.forms import PermissionForm
from app.extensions import db
from app.models import Menu, Permission
from app.utils.decorators import permission_required
from app.utils.logger import log_activity


def _edit_prefix(permission_id):
    return f"{permission_id}-"


def _render_permissions_list(create_form=None, open_modal=None, invalid_edit=None):
    permissions = Permission.query.order_by(Permission.code).all()

    if create_form is None:
        create_form = PermissionForm()

    invalid_permission_id, invalid_form = invalid_edit or (None, None)

    edit_forms = {}
    for permission in permissions:
        if permission.id == invalid_permission_id:
            edit_forms[permission.id] = invalid_form
        else:
            edit_forms[permission.id] = PermissionForm(obj=permission, prefix=_edit_prefix(permission.id))

    # Same guards delete_permission() itself checks before rejecting the
    # request — computed here so the button can be disabled up front.
    delete_reasons = {}
    for permission in permissions:
        menus_using_it = Menu.query.filter_by(permission_code=permission.code).count()
        if permission.roles:
            delete_reasons[permission.id] = f"Assigned to {len(permission.roles)} role(s)."
        elif menus_using_it:
            delete_reasons[permission.id] = f"Required by {menus_using_it} menu item(s)."
        else:
            delete_reasons[permission.id] = None

    return render_template(
        "permissions/list.html",
        permissions=permissions,
        delete_reasons=delete_reasons,
        create_form=create_form,
        edit_forms=edit_forms,
        open_modal=open_modal,
    )


@permissions_bp.route("/")
@permission_required("permission.view")
def list_permissions():
    return _render_permissions_list()


@permissions_bp.route("/create", methods=["POST"])
@permission_required("permission.create")
def create_permission():
    form = PermissionForm()

    if form.validate_on_submit():
        if Permission.query.filter_by(code=form.code.data).first():
            flash("Permission code already exists.", "error")
            return _render_permissions_list(create_form=form, open_modal="create-modal")

        permission = Permission(code=form.code.data, description=form.description.data or None)
        db.session.add(permission)
        db.session.commit()

        log_activity(
            action="CREATE_PERMISSION",
            target_type="permission",
            target_id=str(permission.id),
            description=f"Created permission '{permission.code}'",
        )

        flash(f"Permission '{permission.code}' created.", "success")
        return redirect(url_for("permissions.list_permissions"))

    return _render_permissions_list(create_form=form, open_modal="create-modal")


@permissions_bp.route("/<uuid:permission_id>/edit", methods=["POST"])
@permission_required("permission.edit")
def edit_permission(permission_id):
    permission = Permission.query.get_or_404(permission_id)
    form = PermissionForm(prefix=_edit_prefix(permission_id))

    if form.validate_on_submit():
        duplicate = Permission.query.filter(
            Permission.code == form.code.data, Permission.id != permission.id
        ).first()
        if duplicate:
            flash("Permission code already exists.", "error")
            return _render_permissions_list(
                open_modal=f"edit-modal-{permission_id}", invalid_edit=(permission_id, form)
            )

        old_code = permission.code
        permission.code = form.code.data
        permission.description = form.description.data or None

        # Keep menus that reference this permission by code in sync with a rename.
        if old_code != permission.code:
            Menu.query.filter_by(permission_code=old_code).update({"permission_code": permission.code})

        db.session.commit()

        log_activity(
            action="UPDATE_PERMISSION",
            target_type="permission",
            target_id=str(permission.id),
            description=f"Updated permission '{permission.code}'",
        )

        flash(f"Permission '{permission.code}' updated.", "success")
        return redirect(url_for("permissions.list_permissions"))

    return _render_permissions_list(open_modal=f"edit-modal-{permission_id}", invalid_edit=(permission_id, form))


@permissions_bp.route("/<uuid:permission_id>/delete", methods=["POST"])
@permission_required("permission.delete")
def delete_permission(permission_id):
    permission = Permission.query.get_or_404(permission_id)

    if permission.roles:
        flash(
            f"Permission '{permission.code}' is still assigned to {len(permission.roles)} role(s) "
            "and cannot be deleted.",
            "error",
        )
        return redirect(url_for("permissions.list_permissions"))

    menus_using_it = Menu.query.filter_by(permission_code=permission.code).count()
    if menus_using_it:
        flash(
            f"Permission '{permission.code}' is still required by {menus_using_it} menu item(s) "
            "and cannot be deleted.",
            "error",
        )
        return redirect(url_for("permissions.list_permissions"))

    permission_code = permission.code
    permission_id_str = str(permission.id)
    db.session.delete(permission)
    db.session.commit()

    log_activity(
        action="DELETE_PERMISSION",
        target_type="permission",
        target_id=permission_id_str,
        description=f"Deleted permission '{permission_code}'",
    )

    flash(f"Permission '{permission_code}' deleted.", "success")
    return redirect(url_for("permissions.list_permissions"))
