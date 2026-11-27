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
    def test_parent_with_children_can_be_deleted_with_them(self, menu_client, app):
        with app.app_context():
            parent = Menu(label="Parent", order=0)
            db.session.add(parent)
            db.session.flush()
            child = Menu(label="Child", parent_id=parent.id, order=0)
            db.session.add(child)
            db.session.commit()
            parent_id, child_id = parent.id, child.id

        html = menu_client.get("/menus/").data.decode()
        marker = f"delete-modal-{parent_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" not in button
        assert "1 sub-item(s) will be deleted with it." in html

        menu_client.post(f"/menus/{parent_id}/delete", follow_redirects=True)
        with app.app_context():
            assert Menu.query.get(parent_id) is None
            assert Menu.query.get(child_id) is None

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


class TestSidebarActiveMenu:
    """A page under a menu's URL (e.g. /builders/archived) keeps that menu, and
    so its group, open; the longest matching URL wins."""

    @pytest.mark.parametrize(
        "path, expected",
        [
            ("/builders", "/builders"),
            ("/builders/archived", "/builders"),
            ("/logs/errors", "/logs/errors"),
            ("/logs", "/logs"),
            ("/", "/"),
            ("/buildersx", None),
            ("/unknown", None),
        ],
    )
    def test_active_url(self, app, path, expected):
        from types import SimpleNamespace

        from app.utils.menu_builder import _active_url

        menus = [SimpleNamespace(url=u) for u in ("/", "/builders", "/logs", "/logs/errors", None)]
        with app.test_request_context(path):
            assert _active_url(menus) == expected
