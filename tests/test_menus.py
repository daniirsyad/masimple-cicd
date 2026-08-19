import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Menu, Permission, Role, User

MENU_PASSWORD = "MenuPass123!"


@pytest.fixture
def menu_client(client, app):
    with app.app_context():
        permissions = [Permission(code="menu.view", description="view"), Permission(code="menu.edit", description="edit")]
        db.session.add_all(permissions)
        role = Role(name="MenuAdmin", description="Test menu admin role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(username="menu_test", password_hash=generate_password_hash(MENU_PASSWORD), is_active=True, role_id=role.id)
        db.session.add(user)
        db.session.commit()

    client.post("/login", data={"username": "menu_test", "password": MENU_PASSWORD}, follow_redirects=True)
    return client


class TestDeleteButtonDisabledWhenMenuHasChildren:
    def test_parent_with_children_shows_disabled_delete_button(self, menu_client, app):
        with app.app_context():
            parent = Menu(label="Parent", order=0)
            db.session.add(parent)
            db.session.flush()
            db.session.add(Menu(label="Child", parent_id=parent.id, order=0))
            db.session.commit()
            parent_id = parent.id

        response = menu_client.get("/menus/")
        html = response.data.decode()
        marker = f"delete-modal-{parent_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" in button
        assert "Still has sub-items." in button

    def test_leaf_menu_shows_enabled_delete_button(self, menu_client, app):
        with app.app_context():
            leaf = Menu(label="Leaf", order=0)
            db.session.add(leaf)
            db.session.commit()
            leaf_id = leaf.id

        response = menu_client.get("/menus/")
        html = response.data.decode()
        marker = f"delete-modal-{leaf_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" not in button
