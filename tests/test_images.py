import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    ErrorLog,
    GitSource,
    ImageBuild,
    Permission,
    RegistryTarget,
    Repository,
    Role,
    User,
    Version,
    VersionDocumentation,
    VersionType,
)

IMAGE_PASSWORD = "ImagePass123!"


@pytest.fixture
def image_user(app):
    with app.app_context():
        permission = Permission(code="image.view", description="View images")
        db.session.add(permission)
        role = Role(name="ImageViewer", description="Test image viewer role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="image_test",
            password_hash=generate_password_hash(IMAGE_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def image_client(client, image_user):
    client.post(
        "/login", data={"username": "image_test", "password": IMAGE_PASSWORD}, follow_redirects=True
    )
    return client


@pytest.fixture
def image_and_logs_user(app):
    with app.app_context():
        permissions = [
            Permission(code="image.view", description="View images"),
            Permission(code="logs.view", description="View activity logs"),
        ]
        db.session.add_all(permissions)
        role = Role(name="ImageAndLogsViewer", description="Test image+logs viewer role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="image_logs_test",
            password_hash=generate_password_hash(IMAGE_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def image_and_logs_client(client, image_and_logs_user):
    client.post(
        "/login", data={"username": "image_logs_test", "password": IMAGE_PASSWORD}, follow_redirects=True
    )
    return client


@pytest.fixture
def base_entities(app):
    with app.app_context():
        git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
        db.session.add(git_source)
        db.session.flush()
        repository = Repository(
            git_source_id=git_source.id,
            full_name="org/repo",
            local_path="/tmp/repo",
            status="ready",
            default_branch="main",
        )
        db.session.add(repository)
        registry_target = RegistryTarget(name="reg", provider_type="dockerhub")
        db.session.add(registry_target)
        version_type = VersionType(name="DEV")
        db.session.add(version_type)
        db.session.flush()
        version = Version(name="svc", version_type_id=version_type.id)
        db.session.add(version)
        db.session.flush()
        builder = Builder(
            name="b1",
            version_id=version.id,
            repository_id=repository.id,
            default_branch="main",
            dockerfile_path="Dockerfile",
            registry_target_id=registry_target.id,
        )
        db.session.add(builder)
        db.session.commit()
        return {"version_id": version.id, "builder_id": builder.id}


def _make_batch(base_entities, status="success", bump_type="patch", full_version_string="DEV.0.0.1.010101010101"):
    batch = BuildBatch(
        version_id=base_entities["version_id"],
        full_version_string=full_version_string,
        bump_type=bump_type,
        status=status,
    )
    db.session.add(batch)
    db.session.flush()
    return batch


def _make_image_build(batch, base_entities, status="success", branch_used="main", error_log_id=None):
    image_build = ImageBuild(
        batch_id=batch.id,
        builder_id=base_entities["builder_id"],
        branch_used=branch_used,
        status=status,
        error_log_id=error_log_id,
    )
    db.session.add(image_build)
    db.session.flush()
    return image_build


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/images/").status_code == 403

    def test_status_requires_permission(self, noperm_client):
        assert noperm_client.get("/images/status").status_code == 403


class TestListImages:
    def test_empty_state(self, image_client):
        response = image_client.get("/images/")
        assert response.status_code == 200
        assert b"No batches found." in response.data

    def test_lists_a_batch_with_its_images(self, image_client, app, base_entities):
        with app.app_context():
            batch = _make_batch(base_entities)
            _make_image_build(batch, base_entities)
            db.session.commit()

        response = image_client.get("/images/")
        assert response.status_code == 200
        assert b"DEV.0.0.1.010101010101" in response.data
        assert b"b1" in response.data

    def test_shows_documentation_pending_when_change_type_unset(self, image_client, app, base_entities):
        with app.app_context():
            batch = _make_batch(base_entities)
            _make_image_build(batch, base_entities)
            db.session.add(VersionDocumentation(batch_id=batch.id))
            db.session.commit()

        response = image_client.get("/images/")
        assert response.status_code == 200
        assert b"Documentation Pending" in response.data

    def test_filters_by_batch_status(self, image_client, app, base_entities):
        with app.app_context():
            success_batch = _make_batch(
                base_entities, status="success", full_version_string="DEV.0.0.1.010101010101"
            )
            _make_image_build(success_batch, base_entities, status="success")
            failed_batch = _make_batch(
                base_entities, status="failed", full_version_string="DEV.0.0.2.020202020202"
            )
            _make_image_build(failed_batch, base_entities, status="failed")
            db.session.commit()

        response = image_client.get("/images/?status=failed")
        assert response.status_code == 200
        assert b"DEV.0.0.2.020202020202" in response.data
        assert b"DEV.0.0.1.010101010101" not in response.data

    def test_failed_build_links_to_its_error_log_for_a_user_with_logs_permission(
        self, image_and_logs_client, app, base_entities
    ):
        with app.app_context():
            error_log = ErrorLog(source="worker.run_build", message="boom")
            db.session.add(error_log)
            db.session.flush()
            batch = _make_batch(base_entities, status="failed")
            image_build = _make_image_build(batch, base_entities, status="failed", error_log_id=error_log.id)
            db.session.commit()
            error_log_id = error_log.id
            image_build_id = image_build.id

        response = image_and_logs_client.get("/images/")
        assert response.status_code == 200
        assert f"/logs/errors/{error_log_id}".encode() in response.data

    def test_failed_build_hides_the_error_log_link_without_logs_permission(
        self, image_client, app, base_entities
    ):
        with app.app_context():
            error_log = ErrorLog(source="worker.run_build", message="boom")
            db.session.add(error_log)
            db.session.flush()
            batch = _make_batch(base_entities, status="failed")
            _make_image_build(batch, base_entities, status="failed", error_log_id=error_log.id)
            db.session.commit()
            error_log_id = error_log.id

        response = image_client.get("/images/")
        assert response.status_code == 200
        assert f"/logs/errors/{error_log_id}".encode() not in response.data


class TestStatusEndpoint:
    def test_status_when_idle(self, image_client):
        response = image_client.get("/images/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["busy"] is False
        assert data["running"] is None
        assert data["queue"] == []

    def test_status_with_a_running_batch(self, image_client, app, base_entities):
        with app.app_context():
            batch = _make_batch(base_entities, status="running")
            _make_image_build(batch, base_entities, status="running")
            db.session.commit()

        response = image_client.get("/images/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["busy"] is True
        assert data["running"]["full_version_string"] == "DEV.0.0.1.010101010101"
        assert data["running"]["builder"] == "b1"
        assert data["running"]["progress"] == {"total": 1, "finished": 0, "succeeded": 0}
