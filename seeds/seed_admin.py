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
]

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
        role = seed_super_admin_role(permissions)
        seed_super_admin_user(role)


if __name__ == "__main__":
    run()
