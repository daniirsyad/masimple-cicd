"""Seeds the base permissions, the built-in Super Admin role, and the first Super Admin user.

Safe to re-run: existing permissions/role/user are detected and left untouched.

Usage:
    python seeds/seed_admin.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Permission, Role, User

BASE_PERMISSIONS = [
    ("user.view", "View users"),
    ("user.create", "Create users"),
    ("user.edit", "Edit users"),
    ("user.delete", "Delete users"),
    ("role.view", "View roles"),
    ("role.create", "Create roles"),
    ("role.edit", "Edit roles"),
    ("role.delete", "Delete roles"),
    ("logs.view", "View activity logs"),
    ("menu.view", "View menus"),
    ("menu.edit", "Edit menus"),
    ("permission.view", "View permissions"),
    ("permission.create", "Create permissions"),
    ("permission.edit", "Edit permissions"),
    ("permission.delete", "Delete permissions"),
    ("version.view", "View versions"),
    ("version.manage", "Create and edit versions"),
    ("image.view", "View built images"),
    ("builder.view", "View builders"),
    ("builder.manage", "Create and edit builders"),
    ("builder.build", "Trigger image builds"),
    ("aiprovider.manage", "Manage AI provider settings and prompt templates"),
    ("gitsource.manage", "Manage GitHub connections and registered repositories"),
    ("registry.manage", "Manage container registry targets"),
    ("documentation.edit", "View and edit build batch documentation"),
    ("system.manage", "Manage system configuration (timezone, session timeout)"),
    ("deployment_server.view", "View deployment servers"),
    ("deployment_server.manage", "Create and edit deployment servers"),
    ("deployment_manifest.view", "View deployment manifests"),
    ("deployment_manifest.manage", "Create and edit deployment manifests"),
    ("deployment_run.view", "View deployment run history"),
    ("deployment.deploy", "Deploy a manifest"),
    ("deployment.update", "Update an already-deployed manifest to a newer version"),
    ("deployment.stop", "Stop a deployed manifest"),
    ("deployment.restart", "Restart a deployed manifest's workload(s)"),
    ("deployment_pod.view", "View pod status, logs, and describe output on deployment servers"),
    ("deployment_namespace.manage", "Create, edit, and delete Kubernetes namespaces"),
    ("deployment_secret.manage", "Create, edit, and delete Kubernetes secrets"),
    ("deployment_configmap.manage", "Create, edit, and delete Kubernetes ConfigMaps"),
    ("deployment_workload.restart", "Trigger a rolling restart of a Kubernetes Deployment's pods"),
    ("workflow.view", "View workflows and their run history"),
    ("workflow.manage", "Create, edit, and delete workflows"),
    ("workflow.run", "Trigger a workflow run"),
]

# Retired in favor of the four granular deployment.* permissions above (one
# per action, so a role can e.g. deploy without being able to stop). Kept
# here only so _migrate_deployment_trigger_permission below can find and
# retire it — do not add it back to BASE_PERMISSIONS.
_RETIRED_DEPLOYMENT_TRIGGER_CODE = "deployment.trigger"
_DEPLOYMENT_TRIGGER_REPLACEMENT_CODES = ("deployment.deploy", "deployment.update", "deployment.stop", "deployment.restart")

SUPER_ADMIN_ROLE_NAME = "Super Admin"


def seed_permissions():
    permissions = {}
    for code, description in BASE_PERMISSIONS:
        permission = Permission.query.filter_by(code=code).first()
        if permission is None:
            permission = Permission(code=code, description=description)
            db.session.add(permission)
            print(f"Created permission: {code}")
        permissions[code] = permission
    db.session.commit()
    return permissions


def seed_super_admin_role(permissions):
    role = Role.query.filter_by(name=SUPER_ADMIN_ROLE_NAME).first()
    if role is None:
        role = Role(
            name=SUPER_ADMIN_ROLE_NAME,
            description="Built-in super administrator role",
            is_system=True,
        )
        db.session.add(role)
        print(f"Created role: {SUPER_ADMIN_ROLE_NAME}")

    existing_codes = {permission.code for permission in role.permissions}
    for code, permission in permissions.items():
        if code not in existing_codes:
            role.permissions.append(permission)

    db.session.commit()
    return role


def migrate_deployment_trigger_permission(permissions):
    """One-off data migration: `deployment.trigger` used to gate both Deploy
    and Stop together; it's now split into deployment.deploy/update/stop/
    restart (one permission per action). Any role that already held
    deployment.trigger gets all four replacements granted so existing access
    carries over unchanged, then the retired permission is deleted. Safe to
    re-run — a no-op once deployment.trigger no longer exists.
    """
    trigger_permission = Permission.query.filter_by(code=_RETIRED_DEPLOYMENT_TRIGGER_CODE).first()
    if trigger_permission is None:
        return

    replacements = [permissions[code] for code in _DEPLOYMENT_TRIGGER_REPLACEMENT_CODES]
    for role in list(trigger_permission.roles):
        existing_codes = {permission.code for permission in role.permissions}
        for code, permission in zip(_DEPLOYMENT_TRIGGER_REPLACEMENT_CODES, replacements):
            if code not in existing_codes:
                role.permissions.append(permission)
                print(f"Granted {code} to role '{role.name}' (replacing deployment.trigger)")

    db.session.delete(trigger_permission)
    db.session.commit()
    print("Retired permission: deployment.trigger")


def seed_super_admin_user(role):
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")

    user = User.query.filter_by(username=username).first()
    if user is not None:
        print(f"Super Admin user '{username}' already exists, skipping.")
        return user

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        full_name="Super Admin",
        is_active=True,
        role=role,
    )
    db.session.add(user)
    db.session.commit()
    print(f"Created Super Admin user: {username} (change the password after first login)")
    return user


def run():
    app = create_app()
    with app.app_context():
        permissions = seed_permissions()
        migrate_deployment_trigger_permission(permissions)
        role = seed_super_admin_role(permissions)
        seed_super_admin_user(role)


if __name__ == "__main__":
    run()
