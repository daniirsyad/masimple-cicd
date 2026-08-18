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

# Kept in sync by hand with the direct Menu inserts applied to any
# already-seeded dev DB when these icons were added (see SESSION_START.md) —
# `.sidebar-icon` CSS already forces fill/stroke to currentColor for dark
# mode, so no explicit fill="currentColor" is needed on the paths themselves.
_FILE_TEXT_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>file-text</title><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>'
_CODE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>code</title><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>'
_WORKFLOW_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>workflow</title><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>'

# The rest of the tree shipped with icon=None (blank sidebar icon) until this
# batch — every remaining menu row got a matching Lucide icon here, plus
# migrate_add_missing_icons() below so already-seeded DBs pick them up too
# (get_or_create only fills in icon on brand-new rows).
_HOUSE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>house</title><path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"></path><path d="M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path></svg>'
_BRIEFCASE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>briefcase</title><path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path><rect width="20" height="14" x="2" y="6" rx="2"></rect></svg>'
_USERS_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>users</title><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path><path d="M16 3.128a4 4 0 0 1 0 7.744"></path><path d="M22 21v-2a4 4 0 0 0-3-3.87"></path><circle cx="9" cy="7" r="4"></circle></svg>'
_SHIELD_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>shield</title><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"></path></svg>'
_MENU_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>menu</title><path d="M4 5h16"></path><path d="M4 12h16"></path><path d="M4 19h16"></path></svg>'
_KEY_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>key</title><path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4"></path><path d="m21 2-9.6 9.6"></path><circle cx="7.5" cy="15.5" r="5.5"></circle></svg>'
_CLIPBOARD_LIST_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>clipboard-list</title><rect width="8" height="4" x="8" y="2" rx="1" ry="1"></rect><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path><path d="M12 11h4"></path><path d="M12 16h4"></path><path d="M8 11h.01"></path><path d="M8 16h.01"></path></svg>'
_ACTIVITY_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>activity</title><path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"></path></svg>'
_ALERT_TRIANGLE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>alert-triangle</title><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>'
_PACKAGE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>package</title><path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"></path><path d="M12 22V12"></path><polyline points="3.29 7 12 12 20.71 7"></polyline><path d="m7.5 4.27 9 5.15"></path></svg>'
_GIT_BRANCH_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>git-branch</title><path d="M15 6a9 9 0 0 0-9 9V3"></path><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle></svg>'
_DATABASE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>database</title><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M3 5V19A9 3 0 0 0 21 19V5"></path><path d="M3 12A9 3 0 0 0 21 12"></path></svg>'
_TAG_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>tag</title><path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"></path><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"></circle></svg>'
_HAMMER_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>hammer</title><path d="m15 12-9.373 9.373a1 1 0 0 1-3.001-3L12 9"></path><path d="m18 15 4-4"></path><path d="m21.5 11.5-1.914-1.914A2 2 0 0 1 19 8.172v-.344a2 2 0 0 0-.586-1.414l-1.657-1.657A6 6 0 0 0 12.516 3H9l1.243 1.243A6 6 0 0 1 12 8.485V10l2 2h1.172a2 2 0 0 1 1.414.586L18.5 14.5"></path></svg>'
_IMAGE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>image</title><rect width="18" height="18" x="3" y="3" rx="2" ry="2"></rect><circle cx="9" cy="9" r="2"></circle><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"></path></svg>'
_BOOK_OPEN_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>book-open</title><path d="M12 5v16"></path><path d="M20.001 19A2 2 0 0 0 22 17V5a2 2 0 0 0-1.999-2L16 3.002A5 5 0 0 0 12 5a5 5 0 0 0-4-2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 1.999 2H8a5 5 0 0 1 4 2 5 5 0 0 1 4-2z"></path></svg>'
_BOT_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>bot</title><path d="M12 8V4H8"></path><rect width="16" height="12" x="4" y="8" rx="2"></rect><path d="M2 14h2"></path><path d="M20 14h2"></path><path d="M15 13v2"></path><path d="M9 13v2"></path></svg>'
_ROCKET_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>rocket</title><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"></path><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09"></path><path d="M9 12a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.4 22.4 0 0 1-4 2z"></path><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 .05 5 .05"></path></svg>'
_SERVER_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>server</title><rect width="20" height="8" x="2" y="2" rx="2" ry="2"></rect><rect width="20" height="8" x="2" y="14" rx="2" ry="2"></rect><line x1="6" x2="6.01" y1="6" y2="6"></line><line x1="6" x2="6.01" y1="18" y2="18"></line></svg>'
_FILE_CODE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>file-code</title><path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"></path><path d="M14 2v5a1 1 0 0 0 1 1h5"></path><path d="M10 12.5 8 15l2 2.5"></path><path d="m14 12.5 2 2.5-2 2.5"></path></svg>'
_PLAY_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>play</title><path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"></path></svg>'
_SETTINGS_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>settings</title><path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"></path><circle cx="12" cy="12" r="3"></circle></svg>'
_SLIDERS_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><title>sliders-horizontal</title><path d="M10 5H3"></path><path d="M12 19H3"></path><path d="M14 3v4"></path><path d="M16 17v4"></path><path d="M21 12h-9"></path><path d="M21 19h-5"></path><path d="M21 5h-7"></path><path d="M8 10v4"></path><path d="M8 12H3"></path></svg>'


def get_or_create(label, parent_id=None, **kwargs):
    menu = Menu.query.filter_by(label=label, parent_id=parent_id).first()
    if menu is None:
        menu = Menu(label=label, parent_id=parent_id, **kwargs)
        db.session.add(menu)
        db.session.flush()  # assign menu.id so children can reference it as parent_id
        print(f"Created menu: {label}")
    return menu


def migrate_remove_deployment_pods_menu():
    """One-off data migration: the "Deployment Pods" sidebar entry (linking
    to /deployment-pods, a standalone server-picker page) was retired once
    /deployment-servers grew its own per-row Pods/Namespaces/Secrets/
    ConfigMaps links, making the picker page (and its menu entry) pure
    duplication. /deployment-pods itself now just redirects into
    /deployment-servers rather than 404ing, so nothing breaks for anyone who
    still has this row — this only removes the now-redundant sidebar entry.
    Safe to re-run — a no-op once the row is gone.
    """
    menu = Menu.query.filter_by(label="Deployment Pods", url="/deployment-pods").first()
    if menu is None:
        return
    db.session.delete(menu)
    db.session.commit()
    print("Retired menu: Deployment Pods")


# label -> icon, for every menu row seeded above (icon=... in get_or_create).
# get_or_create only sets icon on a brand-new row, so a DB that was already
# seeded before these icons existed would otherwise keep icon=None forever —
# this backfills it by label on every re-run. Every label in the tree is
# unique, so matching by label alone (no parent_id) is unambiguous.
_LABEL_ICONS = {
    "Home": _HOUSE_ICON,
    "Management": _BRIEFCASE_ICON,
    "Users": _USERS_ICON,
    "Roles": _SHIELD_ICON,
    "Menus": _MENU_ICON,
    "Permissions": _KEY_ICON,
    "Logging": _CLIPBOARD_LIST_ICON,
    "Activity Logs": _ACTIVITY_ICON,
    "Error Logs": _ALERT_TRIANGLE_ICON,
    "Image Builder": _PACKAGE_ICON,
    "GitHub": _GIT_BRANCH_ICON,
    "Registries": _DATABASE_ICON,
    "Versions": _TAG_ICON,
    "Dockerfiles": _FILE_TEXT_ICON,
    "Builders": _HAMMER_ICON,
    "Images": _IMAGE_ICON,
    "Documentation": _BOOK_OPEN_ICON,
    "AI Settings": _BOT_ICON,
    "Deployment": _ROCKET_ICON,
    "Deployment Servers": _SERVER_ICON,
    "Deployment Manifests": _FILE_CODE_ICON,
    "YAML Generator": _CODE_ICON,
    "Deployment Runs": _PLAY_ICON,
    "Workflows": _WORKFLOW_ICON,
    "System": _SETTINGS_ICON,
    "Configuration": _SLIDERS_ICON,
}


def migrate_add_missing_icons():
    """One-off data migration: backfills `icon` on any already-seeded menu
    row that predates this batch of icons and still has icon=None. Safe to
    re-run — a no-op once every row has an icon.
    """
    changed = 0
    for label, icon in _LABEL_ICONS.items():
        menu = Menu.query.filter_by(label=label).first()
        if menu is not None and not menu.icon:
            menu.icon = icon
            changed += 1
    if changed:
        db.session.commit()
        print(f"Backfilled icon on {changed} existing menu row(s)")


def run():
    app = create_app()
    with app.app_context():
        get_or_create(
            "Home", url="/", order=0, icon=_HOUSE_ICON, show_in_navbar=False, show_in_sidebar=True
        )

        management = get_or_create(
            "Management",
            url=None,
            order=5,
            icon=_BRIEFCASE_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        get_or_create(
            "Users",
            parent_id=management.id,
            url="/users",
            permission_code="user.view",
            order=0,
            icon=_USERS_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Roles",
            parent_id=management.id,
            url="/roles",
            permission_code="role.view",
            order=1,
            icon=_SHIELD_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Menus",
            parent_id=management.id,
            url="/menus",
            permission_code="menu.view",
            order=3,
            icon=_MENU_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Permissions",
            parent_id=management.id,
            url="/permissions",
            permission_code="permission.view",
            order=4,
            icon=_KEY_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        # "Logging" was split out from "Management" into its own top-level
        # group at some point after this script was first written; matched
        # here (not nested under management.id) so re-running this script
        # doesn't recreate "Activity Logs" as a duplicate under Management.
        logging_group = get_or_create(
            "Logging",
            url=None,
            order=4,
            icon=_CLIPBOARD_LIST_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Activity Logs",
            parent_id=logging_group.id,
            url="/logs",
            permission_code="logs.view",
            order=0,
            icon=_ACTIVITY_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Error Logs",
            parent_id=logging_group.id,
            url="/logs/errors",
            permission_code="logs.view",
            order=1,
            icon=_ALERT_TRIANGLE_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        image_builder = get_or_create(
            "Image Builder",
            url=None,
            order=2,
            icon=_PACKAGE_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        # Order follows a logical setup-to-usage flow: connect a repo/registry,
        # define a Version, create a Builder (optionally with a managed
        # Dockerfile), build, then browse the results/documentation.
        get_or_create(
            "GitHub",
            parent_id=image_builder.id,
            url="/github",
            permission_code="gitsource.manage",
            order=0,
            icon=_GIT_BRANCH_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Registries",
            parent_id=image_builder.id,
            url="/registries",
            permission_code="registry.manage",
            order=1,
            icon=_DATABASE_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Versions",
            parent_id=image_builder.id,
            url="/versions",
            permission_code="version.view",
            order=2,
            icon=_TAG_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Dockerfiles",
            parent_id=image_builder.id,
            url="/dockerfiles",
            permission_code="dockerfile.manage",
            order=3,
            icon=_FILE_TEXT_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Builders",
            parent_id=image_builder.id,
            url="/builders",
            permission_code="builder.view",
            order=4,
            icon=_HAMMER_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Images",
            parent_id=image_builder.id,
            url="/images",
            permission_code="image.view",
            order=5,
            icon=_IMAGE_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Documentation",
            parent_id=image_builder.id,
            url="/documentation",
            permission_code="documentation.edit",
            order=6,
            icon=_BOOK_OPEN_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "AI Settings",
            parent_id=image_builder.id,
            url="/ai-settings",
            permission_code="aiprovider.manage",
            order=7,
            icon=_BOT_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        # Added after "System" (order=4) was already seeded on existing
        # installs — get_or_create leaves already-existing rows' order
        # untouched, so this uses order=5 rather than renumbering System, to
        # avoid a same-parent order tie on a re-run against a DB that already
        # has "System" at order=4.
        deployment = get_or_create(
            "Deployment",
            url=None,
            order=3,
            icon=_ROCKET_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        get_or_create(
            "Deployment Servers",
            parent_id=deployment.id,
            url="/deployment-servers",
            permission_code="deployment_server.view",
            order=0,
            icon=_SERVER_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Deployment Manifests",
            parent_id=deployment.id,
            url="/deployment-manifests",
            permission_code="deployment_manifest.view",
            order=1,
            icon=_FILE_CODE_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        # No "Deployment Pods" entry here anymore — retired in favor of
        # per-row links on Deployment Servers, see
        # migrate_remove_deployment_pods_menu below.

        # Sits next to the Manifests it feeds ("Save as Manifest" hands off
        # into the Deployment Manifest create flow), ahead of Runs.
        get_or_create(
            "YAML Generator",
            parent_id=deployment.id,
            url="/yaml-generator",
            permission_code="yaml_generator.view",
            order=2,
            icon=_CODE_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Deployment Runs",
            parent_id=deployment.id,
            url="/deployment-runs",
            permission_code="deployment_run.view",
            order=3,
            icon=_PLAY_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        # Elevated to right after Home — the orchestration layer tying the
        # Image Builder and Deployment modules together, not a peer of the
        # admin/config groups below it.
        get_or_create(
            "Workflows",
            url="/workflows",
            permission_code="workflow.view",
            order=1,
            icon=_WORKFLOW_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        system_group = get_or_create(
            "System",
            url=None,
            order=6,
            icon=_SETTINGS_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )
        get_or_create(
            "Configuration",
            parent_id=system_group.id,
            url="/config",
            permission_code="system.manage",
            order=0,
            icon=_SLIDERS_ICON,
            show_in_navbar=False,
            show_in_sidebar=True,
        )

        db.session.commit()

        migrate_remove_deployment_pods_menu()
        migrate_add_missing_icons()


if __name__ == "__main__":
    run()
