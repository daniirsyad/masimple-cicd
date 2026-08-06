"""Seeds the default sidebar/navbar menu tree: Home + Management > Users/Roles/Activity Logs.

Safe to re-run: existing menu items (matched by label + parent) are detected and left untouched.

Usage:
    python seeds/seed_menu.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import Menu


def get_or_create(label, parent_id=None, **kwargs):
    menu = Menu.query.filter_by(label=label, parent_id=parent_id).first()
    if menu is None:
        menu = Menu(label=label, parent_id=parent_id, **kwargs)
        db.session.add(menu)
        db.session.flush()  # assign menu.id so children can reference it as parent_id
        print(f"Created menu: {label}")
    return menu


def run():
    app = create_app()
    with app.app_context():
        get_or_create("Home", url="/", order=0, show_in_navbar=False, show_in_sidebar=True)

        management = get_or_create(
            "Management", url=None, order=1, show_in_navbar=False, show_in_sidebar=True
        )

        get_or_create(
            "Users",
            parent_id=management.id,
            url="/users",
            permission_code="user.view",
            order=0,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Roles",
            parent_id=management.id,
            url="/roles",
            permission_code="role.view",
            order=1,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Activity Logs",
            parent_id=management.id,
            url="/logs",
            permission_code="logs.view",
            order=2,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Menus",
            parent_id=management.id,
            url="/menus",
            permission_code="menu.view",
            order=3,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Permissions",
            parent_id=management.id,
            url="/permissions",
            permission_code="permission.view",
            order=4,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        db.session.commit()


if __name__ == "__main__":
    run()
