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


def _active_url(menus):
    """The menu URL the current page belongs to: its own URL, or the longest one
    it sits under (/builders/archived -> /builders; /logs/errors -> /logs/errors,
    not /logs). "/" only matches itself."""
    path = _normalize_path(request.path)
    urls = {_normalize_path(m.url) for m in menus if m.url}
    matches = [u for u in urls if u == path or (u != "/" and path.startswith(u + "/"))]
    return max(matches, key=len, default=None)


def _to_dict(menu, active_url, children=None):
    children = children or []
    return {
        "id": menu.id,
        "label": menu.label,
        "icon": menu.icon,
        "url": menu.url,
        "children": children,
        "active": menu.url is not None and _normalize_path(menu.url) == active_url,
        "has_active_child": any(child["active"] for child in children),
    }


def build_sidebar_menu(user, menus=None):
    """Build the nested (max 1-level) sidebar tree, filtered by the user's permissions."""
    if menus is None:
        menus = Menu.query.order_by(Menu.order).all()
    all_menus = [m for m in menus if m.show_in_sidebar]
    active_url = _active_url(all_menus)

    children_by_parent = {}
    for menu in all_menus:
        if menu.parent_id is not None:
            children_by_parent.setdefault(menu.parent_id, []).append(menu)

    tree = []
    for menu in all_menus:
        if menu.parent_id is not None or not _visible(menu, user):
            continue

        children = [
            _to_dict(child, active_url) for child in children_by_parent.get(menu.id, []) if _visible(child, user)
        ]

        # A parent-only group (no url) with no visible children has nothing to show.
        if menu.url is None and not children:
            continue

        tree.append(_to_dict(menu, active_url, children))

    return tree


def build_navbar_menu(user, menus=None):
    """Flat list of menu items flagged for the navbar, filtered by the user's permissions."""
    if menus is None:
        menus = Menu.query.order_by(Menu.order).all()
    all_menus = [m for m in menus if m.show_in_navbar]
    active_url = _active_url(all_menus)
    return [_to_dict(menu, active_url) for menu in all_menus if _visible(menu, user)]


def menu_builder(user):
    """Returns the sidebar/navbar menu context used by base.html and its partials."""
    menus = Menu.query.order_by(Menu.order).all()  # one query for both
    return {
        "sidebar_menu": build_sidebar_menu(user, menus),
        "navbar_menu": build_navbar_menu(user, menus),
    }
