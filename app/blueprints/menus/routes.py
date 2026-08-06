import uuid

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.blueprints.menus import menus_bp
from app.blueprints.menus.forms import MenuForm
from app.extensions import db
from app.models import Menu, Permission
from app.utils.decorators import permission_required
from app.utils.logger import log_activity


def _top_level_choices(exclude_id=None):
    query = Menu.query.filter_by(parent_id=None).order_by(Menu.order)
    if exclude_id:
        query = query.filter(Menu.id != exclude_id)
    return [("", "— None (top-level) —")] + [(str(menu.id), menu.label) for menu in query.all()]


def _permission_choices():
    codes = [permission.code for permission in Permission.query.order_by(Permission.code).all()]
    return [("", "— None —")] + [(code, code) for code in codes]


def _next_order(parent_id):
    max_order = db.session.query(db.func.max(Menu.order)).filter(Menu.parent_id == parent_id).scalar()
    return (max_order + 1) if max_order is not None else 0


def _edit_prefix(menu_id):
    return f"{menu_id}-"


def _render_menus_list(create_form=None, open_modal=None, invalid_edit=None):
    top_level = Menu.query.filter_by(parent_id=None).order_by(Menu.order).all()

    children_by_parent = {}
    for menu in Menu.query.filter(Menu.parent_id.isnot(None)).order_by(Menu.order).all():
        children_by_parent.setdefault(menu.parent_id, []).append(menu)

    all_menus = top_level + [child for children in children_by_parent.values() for child in children]

    if create_form is None:
        create_form = MenuForm()
    create_form.parent_id.choices = _top_level_choices()
    create_form.permission_code.choices = _permission_choices()

    invalid_menu_id, invalid_form = invalid_edit or (None, None)

    edit_forms = {}
    has_children_map = {}
    for menu in all_menus:
        has_children_map[menu.id] = bool(children_by_parent.get(menu.id))

        if menu.id == invalid_menu_id:
            edit_forms[menu.id] = invalid_form
        else:
            form = MenuForm(obj=menu, prefix=_edit_prefix(menu.id))
            form.parent_id.choices = _top_level_choices(exclude_id=menu.id)
            form.permission_code.choices = _permission_choices()
            form.parent_id.data = str(menu.parent_id) if menu.parent_id else ""
            form.permission_code.data = menu.permission_code or ""
            edit_forms[menu.id] = form

    return render_template(
        "menus/list.html",
        top_level=top_level,
        children_by_parent=children_by_parent,
        create_form=create_form,
        edit_forms=edit_forms,
        has_children_map=has_children_map,
        open_modal=open_modal,
    )


@menus_bp.route("/")
@permission_required("menu.view")
def list_menus():
    return _render_menus_list()


@menus_bp.route("/create", methods=["POST"])
@permission_required("menu.edit")
def create_menu():
    form = MenuForm()
    form.parent_id.choices = _top_level_choices()
    form.permission_code.choices = _permission_choices()

    if form.validate_on_submit():
        parent_id = uuid.UUID(form.parent_id.data) if form.parent_id.data else None

        menu = Menu(
            label=form.label.data,
            icon=form.icon.data or None,
            url=form.url.data or None,
            parent_id=parent_id,
            permission_code=form.permission_code.data or None,
            order=_next_order(parent_id),
            show_in_navbar=form.show_in_navbar.data,
            show_in_sidebar=form.show_in_sidebar.data,
        )
        db.session.add(menu)
        db.session.commit()

        log_activity(
            action="CREATE_MENU",
            target_type="menu",
            target_id=str(menu.id),
            description=f"Created menu '{menu.label}'",
        )

        flash(f"Menu '{menu.label}' created.", "success")
        return redirect(url_for("menus.list_menus"))

    return _render_menus_list(create_form=form, open_modal="create-modal")


@menus_bp.route("/<uuid:menu_id>/edit", methods=["POST"])
@permission_required("menu.edit")
def edit_menu(menu_id):
    menu = Menu.query.get_or_404(menu_id)
    has_children = bool(menu.children)

    form = MenuForm(prefix=_edit_prefix(menu_id))
    form.parent_id.choices = _top_level_choices(exclude_id=menu.id)
    form.permission_code.choices = _permission_choices()

    if form.validate_on_submit():
        parent_id = uuid.UUID(form.parent_id.data) if form.parent_id.data else None

        if has_children and parent_id is not None:
            flash(
                "This menu has sub-items, so it must stay a top-level item (max nesting depth is 1).",
                "error",
            )
            return _render_menus_list(open_modal=f"edit-modal-{menu_id}", invalid_edit=(menu_id, form))

        if parent_id != menu.parent_id:
            menu.order = _next_order(parent_id)

        menu.label = form.label.data
        menu.icon = form.icon.data or None
        menu.url = form.url.data or None
        menu.parent_id = parent_id
        menu.permission_code = form.permission_code.data or None
        menu.show_in_navbar = form.show_in_navbar.data
        menu.show_in_sidebar = form.show_in_sidebar.data

        db.session.commit()

        log_activity(
            action="UPDATE_MENU",
            target_type="menu",
            target_id=str(menu.id),
            description=f"Updated menu '{menu.label}'",
        )

        flash(f"Menu '{menu.label}' updated.", "success")
        return redirect(url_for("menus.list_menus"))

    return _render_menus_list(open_modal=f"edit-modal-{menu_id}", invalid_edit=(menu_id, form))


@menus_bp.route("/<uuid:menu_id>/delete", methods=["POST"])
@permission_required("menu.edit")
def delete_menu(menu_id):
    menu = Menu.query.get_or_404(menu_id)

    if menu.children:
        flash(f"Menu '{menu.label}' still has sub-items and cannot be deleted.", "error")
        return redirect(url_for("menus.list_menus"))

    menu_label = menu.label
    menu_id_str = str(menu.id)
    db.session.delete(menu)
    db.session.commit()

    log_activity(
        action="DELETE_MENU",
        target_type="menu",
        target_id=menu_id_str,
        description=f"Deleted menu '{menu_label}'",
    )

    flash(f"Menu '{menu_label}' deleted.", "success")
    return redirect(url_for("menus.list_menus"))


@menus_bp.route("/reorder", methods=["POST"])
@permission_required("menu.edit")
def reorder_menus():
    payload = request.get_json(silent=True) or {}
    top_level_ids = payload.get("top_level", [])
    children_map = payload.get("children", {})

    if not isinstance(top_level_ids, list) or not isinstance(children_map, dict):
        return jsonify({"error": "Invalid payload."}), 400

    child_ids = {child_id for child_list in children_map.values() for child_id in child_list}

    # Enforce the max-depth-1 rule server-side: a menu can't be both a parent
    # (has an entry in children_map) and someone else's child at the same time.
    if set(children_map.keys()) & child_ids:
        return jsonify({"error": "Cannot nest a menu under another menu that itself has sub-items."}), 400

    try:
        all_ids = (
            {uuid.UUID(menu_id) for menu_id in top_level_ids}
            | {uuid.UUID(menu_id) for menu_id in child_ids}
            | {uuid.UUID(parent_id) for parent_id in children_map}
        )
    except (ValueError, AttributeError):
        return jsonify({"error": "Invalid menu id."}), 400

    menus = {menu.id: menu for menu in Menu.query.filter(Menu.id.in_(all_ids)).all()}
    if len(menus) != len(all_ids):
        return jsonify({"error": "One or more menu items no longer exist."}), 400

    for index, menu_id in enumerate(top_level_ids):
        menu = menus[uuid.UUID(menu_id)]
        menu.parent_id = None
        menu.order = index

    for parent_id, child_id_list in children_map.items():
        parent_uuid = uuid.UUID(parent_id)
        for index, child_id in enumerate(child_id_list):
            child = menus[uuid.UUID(child_id)]
            child.parent_id = parent_uuid
            child.order = index

    db.session.commit()

    log_activity(action="REORDER_MENUS", target_type="menu", description="Reordered the menu tree")

    return jsonify({"status": "ok"})
