from flask import request

from app.models import Menu


def _visible(menu, user):
    """A menu item with no permission_code is visible to anyone; otherwise the user needs it."""
    if menu.permission_code is None:
        return True
    return bool(user and user.is_authenticated and user.has_permission(menu.permission_code))


def _normalize_path(path):
    """Strip a trailing slash for comparison, except for the root path itself."""
    if not path:
        return None
    return path if path == "/" else path.rstrip("/")


def _is_active(url):
    if not url:
        return False
    return _normalize_path(url) == _normalize_path(request.path)


def _to_dict(menu, children=None):
    children = children or []
    return {
        "id": menu.id,
        "label": menu.label,
        "icon": menu.icon,
        "url": menu.url,
        "children": children,
        "active": _is_active(menu.url),
        "has_active_child": any(child["active"] for child in children),
    }


def build_sidebar_menu(user):
    """Build the nested (max 1-level) sidebar tree, filtered by the user's permissions."""
    all_menus = Menu.query.filter_by(show_in_sidebar=True).order_by(Menu.order).all()

    children_by_parent = {}
    for menu in all_menus:
        if menu.parent_id is not None:
            children_by_parent.setdefault(menu.parent_id, []).append(menu)

    tree = []
    for menu in all_menus:
        if menu.parent_id is not None or not _visible(menu, user):
            continue

        children = [
            _to_dict(child) for child in children_by_parent.get(menu.id, []) if _visible(child, user)
        ]

        # A parent-only group (no url) with no visible children has nothing to show.
        if menu.url is None and not children:
            continue

        tree.append(_to_dict(menu, children))

    return tree


def build_navbar_menu(user):
    """Flat list of menu items flagged for the navbar, filtered by the user's permissions."""
    all_menus = Menu.query.filter_by(show_in_navbar=True).order_by(Menu.order).all()
    return [_to_dict(menu) for menu in all_menus if _visible(menu, user)]


def menu_builder(user):
    """Returns the sidebar/navbar menu context used by base.html and its partials."""
    return {
        "sidebar_menu": build_sidebar_menu(user),
        "navbar_menu": build_navbar_menu(user),
    }
