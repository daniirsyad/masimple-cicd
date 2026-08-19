import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Permission, Role, User

ROLE_ADMIN_PASSWORD = "RoleAdminPass123!"


@pytest.fixture
def some_permissions(app):
    """A handful of permissions across different resources, to exercise the
    role picker's grouping-by-resource behavior."""
    with app.app_context():
        permissions = [
            Permission(code="user.view", description="View users"),
            Permission(code="user.create", description="Create users"),
            Permission(code="builder.view", description="View builders"),
            Permission(code="builder.manage", description="Create and edit builders"),
        ]
        db.session.add_all(permissions)
        db.session.commit()
        return {p.code: p.id for p in permissions}


@pytest.fixture
def role_admin_user(app, some_permissions):
    with app.app_context():
        role_perms = [
            Permission(code="role.view", description="View roles"),
            Permission(code="role.create", description="Create roles"),
            Permission(code="role.edit", description="Edit roles"),
        ]
        db.session.add_all(role_perms)
        role = Role(name="RoleAdmin", description="Test role admin role")
        role.permissions = role_perms
        db.session.add(role)
        db.session.flush()

        user = User(
            username="role_admin_test",
            password_hash=generate_password_hash(ROLE_ADMIN_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def role_admin_client(client, role_admin_user):
    client.post(
        "/login",
        data={"username": "role_admin_test", "password": ROLE_ADMIN_PASSWORD},
        follow_redirects=True,
    )
    return client


class TestDeleteButtonDisabledForRolesWithAssignedUsers:
    def test_role_with_assigned_users_shows_disabled_delete_button(self, role_admin_client, app):
        with app.app_context():
            role = Role(name="Assigned", description="has a user")
            db.session.add(role)
            db.session.flush()
            db.session.add(
                User(
                    username="assigned_to_role",
                    password_hash=generate_password_hash("Whatever123!"),
                    is_active=True,
                    role_id=role.id,
                )
            )
            db.session.commit()
            role_id = role.id

        response = role_admin_client.get("/roles/")
        html = response.data.decode()
        marker = f"delete-modal-{role_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" in button
        assert "Assigned to 1 user(s)." in button

    def test_role_with_no_assigned_users_shows_enabled_delete_button(self, role_admin_client, app):
        with app.app_context():
            role = Role(name="Unassigned", description="no users")
            db.session.add(role)
            db.session.commit()
            role_id = role.id

        response = role_admin_client.get("/roles/")
        html = response.data.decode()
        marker = f"delete-modal-{role_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" not in button


class TestPermissionPicker:
    def test_groups_permissions_by_resource_with_friendly_descriptions(
        self, role_admin_client, some_permissions
    ):
        response = role_admin_client.get("/roles/")
        assert response.status_code == 200
        body = response.data.decode()

        # Friendly section headings, not raw resource prefixes.
        assert "Users" in body
        assert "Builders" in body
        # Human-readable descriptions are shown as the checkbox label...
        assert "Create and edit builders" in body
        # ...while the raw code is demoted to a tooltip, not the visible label.
        assert 'title="builder.manage"' in body

    def test_create_role_persists_selected_permissions(
        self, role_admin_client, app, some_permissions
    ):
        response = role_admin_client.post(
            "/roles/create",
            data={
                "name": "Builder Viewer",
                "description": "Can only view builders",
                "permissions": [str(some_permissions["builder.view"])],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            role = Role.query.filter_by(name="Builder Viewer").first()
            assert role is not None
            assert [p.code for p in role.permissions] == ["builder.view"]

    def test_edit_role_updates_permission_selection(self, role_admin_client, app, some_permissions):
        with app.app_context():
            role = Role(name="Shifting Role")
            role.permissions = Permission.query.filter_by(code="user.view").all()
            db.session.add(role)
            db.session.commit()
            role_id = role.id

        response = role_admin_client.post(
            f"/roles/{role_id}/edit",
            data={
                f"{role_id}-name": "Shifting Role",
                f"{role_id}-permissions": [
                    str(some_permissions["builder.view"]),
                    str(some_permissions["builder.manage"]),
                ],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            role = Role.query.get(role_id)
            assert sorted(p.code for p in role.permissions) == ["builder.manage", "builder.view"]
