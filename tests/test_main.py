import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import BuildBatch, Permission, RegistryTarget, Repository, Role, User, Version, VersionType

DASHBOARD_PASSWORD = "DashboardPass123!"


@pytest.fixture
def dashboard_user(app):
    """A role with every Image Builder module view permission, so all of the
    dashboard's conditional stat cards/widgets render at once."""
    with app.app_context():
        codes = ["builder.view", "version.view", "image.view", "documentation.edit", "logs.view"]
        permissions = [Permission(code=code, description=code) for code in codes]
        db.session.add_all(permissions)
        role = Role(name="DashboardViewer", description="Test dashboard role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="dashboard_test",
            password_hash=generate_password_hash(DASHBOARD_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def dashboard_client(client, dashboard_user):
    client.post(
        "/login", data={"username": "dashboard_test", "password": DASHBOARD_PASSWORD}, follow_redirects=True
    )
    return client


class TestDashboard:
    def test_module_stats_are_hidden_without_the_matching_permission(self, noperm_client):
        response = noperm_client.get("/")
        assert response.status_code == 200
        body = response.data.decode()
        assert "Active Users" in body
        assert "Roles" in body
        for label in ("Builders", "Versions", "Images Built", "Documentation Pending"):
            assert label not in body
        assert "Recent Builds" not in body

    def test_module_stats_are_shown_with_the_matching_permissions(self, dashboard_client):
        response = dashboard_client.get("/")
        assert response.status_code == 200
        body = response.data.decode()
        for label in ("Builders", "Versions", "Images Built", "Documentation Pending"):
            assert label in body
        assert "Build engine" in body
        assert "days" in body

    def test_recent_builds_table_lists_the_latest_batch(self, dashboard_client, app):
        with app.app_context():
            registry_target = RegistryTarget(name="reg", provider_type="dockerhub")
            db.session.add(registry_target)
            version_type = VersionType(name="DEV")
            db.session.add(version_type)
            db.session.flush()
            version = Version(name="svc", version_type_id=version_type.id)
            db.session.add(version)
            db.session.flush()
            batch = BuildBatch(
                version_id=version.id,
                full_version_string="DEV.0.0.1.010101010101",
                bump_type="patch",
                status="success",
            )
            db.session.add(batch)
            db.session.commit()

        response = dashboard_client.get("/")
        assert b"DEV.0.0.1.010101010101" in response.data

    def test_documentation_pending_count_only_counts_undocumented_successful_batches(
        self, dashboard_client, app
    ):
        with app.app_context():
            from app.models import VersionDocumentation

            registry_target = RegistryTarget(name="reg", provider_type="dockerhub")
            db.session.add(registry_target)
            version_type = VersionType(name="DEV")
            db.session.add(version_type)
            db.session.flush()
            version = Version(name="svc", version_type_id=version_type.id)
            db.session.add(version)
            db.session.flush()
            batch = BuildBatch(
                version_id=version.id, full_version_string="DEV.0.0.1.x", bump_type="patch", status="success"
            )
            db.session.add(batch)
            db.session.flush()
            db.session.add(VersionDocumentation(batch_id=batch.id))
            db.session.commit()

        response = dashboard_client.get("/")
        body = response.data.decode()
        assert 'Documentation Pending' in body
        # One undocumented successful batch staged above.
        assert '<div class="stat-value">1</div>' in body
