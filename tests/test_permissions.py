import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Menu, Permission, Role, User

PERMISSION_PASSWORD = "PermPass123!"


@pytest.fixture
def permission_client(client, app):
    with app.app_context():
        permissions = [
            Permission(code="permission.view", description="view"),
            Permission(code="permission.delete", description="delete"),
        ]
        db.session.add_all(permissions)
        role = Role(name="PermissionAdmin", description="Test permission admin role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="permission_test", password_hash=generate_password_hash(PERMISSION_PASSWORD), is_active=True, role_id=role.id
        )
        db.session.add(user)
        db.session.commit()

    client.post("/login", data={"username": "permission_test", "password": PERMISSION_PASSWORD}, follow_redirects=True)
    return client


class TestDeleteButtonDisabled:
    def test_permission_assigned_to_a_role_shows_disabled_delete_button(self, permission_client, app):
        with app.app_context():
            permission = Permission(code="widget.manage", description="Manage widgets")
            db.session.add(permission)
            db.session.flush()
            role = Role(name="WidgetAdmin")
            role.permissions = [permission]
            db.session.add(role)
            db.session.commit()
            permission_id = permission.id

        response = permission_client.get("/permissions/")
        html = response.data.decode()
        marker = f"delete-modal-{permission_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" in button
        assert "Assigned to 1 role(s)." in button

    def test_permission_required_by_a_menu_shows_disabled_delete_button(self, permission_client, app):
        with app.app_context():
            permission = Permission(code="widget.manage", description="Manage widgets")
            db.session.add(permission)
            db.session.add(Menu(label="Widgets", permission_code="widget.manage", order=0))
            db.session.commit()
            permission_id = permission.id

        response = permission_client.get("/permissions/")
        html = response.data.decode()
        marker = f"delete-modal-{permission_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" in button
        assert "Required by 1 menu item(s)." in button

    def test_unused_permission_shows_enabled_delete_button(self, permission_client, app):
        with app.app_context():
            permission = Permission(code="widget.manage", description="Manage widgets")
            db.session.add(permission)
            db.session.commit()
            permission_id = permission.id

        response = permission_client.get("/permissions/")
        html = response.data.decode()
        marker = f"delete-modal-{permission_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" not in button
