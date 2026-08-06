import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Permission, Role, User

ADMIN_PASSWORD = "AdminPass123!"
NOPERM_PASSWORD = "NoPermPass123!"

USER_PERMISSION_CODES = ["user.view", "user.create", "user.edit", "user.delete"]


@pytest.fixture(scope="session")
def app():
    flask_app = create_app("testing")

    with flask_app.app_context():
        db.create_all()

    yield flask_app

    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _clean_db(app):
    """Truncate all tables after every test so fixtures start from a blank slate."""
    yield
    with app.app_context():
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_user(app):
    """A user whose role grants all user.* permissions."""
    with app.app_context():
        permissions = [Permission(code=code, description=code) for code in USER_PERMISSION_CODES]
        db.session.add_all(permissions)

        role = Role(name="Admin", description="Test admin role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="admin_test",
            password_hash=generate_password_hash(ADMIN_PASSWORD),
            full_name="Admin Test",
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def noperm_user(app):
    """A user whose role grants no permissions at all."""
    with app.app_context():
        role = Role(name="NoPerm", description="Role with no permissions")
        db.session.add(role)
        db.session.flush()

        user = User(
            username="noperm_test",
            password_hash=generate_password_hash(NOPERM_PASSWORD),
            full_name="No Perm Test",
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


@pytest.fixture
def admin_client(client, admin_user):
    """Test client logged in as a user with full user.* permissions."""
    _login(client, "admin_test", ADMIN_PASSWORD)
    return client


@pytest.fixture
def noperm_client(client, noperm_user):
    """Test client logged in as a user with no permissions."""
    _login(client, "noperm_test", NOPERM_PASSWORD)
    return client
