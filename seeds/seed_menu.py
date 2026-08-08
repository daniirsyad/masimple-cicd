"""Seeds the default sidebar/navbar menu tree: Home, Management > Users/Roles/
Menus/Permissions, Logging > Activity Logs, and Image Builder > Versions/Images/Builder.

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

        # "Logging" was split out from "Management" into its own top-level
        # group at some point after this script was first written; matched
        # here (not nested under management.id) so re-running this script
        # doesn't recreate "Activity Logs" as a duplicate under Management.
        logging_group = get_or_create(
            "Logging", url=None, order=2, show_in_navbar=False, show_in_sidebar=True
        )
        get_or_create(
            "Activity Logs",
            parent_id=logging_group.id,
            url="/logs",
            permission_code="logs.view",
            order=0,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Error Logs",
            parent_id=logging_group.id,
            url="/logs/errors",
            permission_code="logs.view",
            order=1,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        image_builder = get_or_create(
            "Image Builder", url=None, order=3, show_in_navbar=False, show_in_sidebar=True
        )

        get_or_create(
            "Versions",
            parent_id=image_builder.id,
            url="/versions",
            permission_code="version.view",
            order=0,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Images",
            parent_id=image_builder.id,
            url="/images",
            permission_code="image.view",
            order=1,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Builders",
            parent_id=image_builder.id,
            url="/builders",
            permission_code="builder.view",
            order=2,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "AI Settings",
            parent_id=image_builder.id,
            url="/ai-settings",
            permission_code="aiprovider.manage",
            order=3,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "GitHub",
            parent_id=image_builder.id,
            url="/github",
            permission_code="gitsource.manage",
            order=4,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Registries",
            parent_id=image_builder.id,
            url="/registries",
            permission_code="registry.manage",
            order=5,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Documentation",
            parent_id=image_builder.id,
            url="/documentation",
            permission_code="documentation.edit",
            order=6,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        # Added after "System" (order=4) was already seeded on existing
        # installs — get_or_create leaves already-existing rows' order
        # untouched, so this uses order=5 rather than renumbering System, to
        # avoid a same-parent order tie on a re-run against a DB that already
        # has "System" at order=4.
        deployment = get_or_create(
            "Deployment", url=None, order=5, show_in_navbar=False, show_in_sidebar=True
        )

        get_or_create(
            "Deployment Servers",
            parent_id=deployment.id,
            url="/deployment-servers",
            permission_code="deployment_server.view",
            order=0,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Deployment Manifests",
            parent_id=deployment.id,
            url="/deployment-manifests",
            permission_code="deployment_manifest.view",
            order=1,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Deployment Runs",
            parent_id=deployment.id,
            url="/deployment-runs",
            permission_code="deployment_run.view",
            order=2,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Deployment Pods",
            parent_id=deployment.id,
            url="/deployment-pods",
            permission_code="deployment_pod.view",
            order=3,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        system_group = get_or_create(
            "System", url=None, order=4, show_in_navbar=False, show_in_sidebar=True
        )
        get_or_create(
            "Configuration",
            parent_id=system_group.id,
            url="/config",
            permission_code="system.manage",
            order=0,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        db.session.commit()


if __name__ == "__main__":
    run()
