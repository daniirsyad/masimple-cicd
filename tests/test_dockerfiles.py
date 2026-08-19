import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Builder, Dockerfile, GitSource, Permission, RegistryTarget, Repository, Role, User, Version, VersionType

DOCKERFILE_PASSWORD = "DockerfilePass123!"


@pytest.fixture
def dockerfile_user(app):
    with app.app_context():
        permission = Permission(code="dockerfile.manage", description="Manage Dockerfiles")
        db.session.add(permission)
        role = Role(name="DockerfileAdmin", description="Test Dockerfile admin role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="dockerfile_test",
            password_hash=generate_password_hash(DOCKERFILE_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def dockerfile_client(client, dockerfile_user):
    client.post(
        "/login", data={"username": "dockerfile_test", "password": DOCKERFILE_PASSWORD}, follow_redirects=True
    )
    return client


def _make_dockerfile(name="base", content="FROM python:3.12-slim\n"):
    dockerfile = Dockerfile(name=name, content=content)
    db.session.add(dockerfile)
    db.session.flush()
    return dockerfile


def _make_builder_referencing(dockerfile_id):
    git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
    db.session.add(git_source)
    db.session.flush()
    repository = Repository(git_source_id=git_source.id, full_name="org/repo", local_path="/tmp/x", status="ready")
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
        registry_target_id=registry_target.id,
        dockerfile_source="managed",
        managed_dockerfile_id=dockerfile_id,
    )
    db.session.add(builder)
    db.session.flush()
    return builder


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/dockerfiles/").status_code == 403

    def test_create_requires_permission(self, noperm_client):
        assert noperm_client.post("/dockerfiles/create", data={}).status_code == 403


class TestCreateDockerfile:
    def test_successful_creation(self, dockerfile_client, app):
        response = dockerfile_client.post(
            "/dockerfiles/create",
            data={
                "create-dockerfile-name": "python-base",
                "create-dockerfile-content": "FROM python:3.12-slim\nCOPY . /app\n",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            dockerfile = Dockerfile.query.filter_by(name="python-base").first()
            assert dockerfile is not None
            assert dockerfile.content == "FROM python:3.12-slim\nCOPY . /app\n"

    def test_missing_content_is_rejected(self, dockerfile_client, app):
        dockerfile_client.post(
            "/dockerfiles/create",
            data={"create-dockerfile-name": "no-content"},
        )
        with app.app_context():
            assert Dockerfile.query.filter_by(name="no-content").first() is None


class TestEditDockerfile:
    def test_updates_name_and_content(self, dockerfile_client, app):
        with app.app_context():
            dockerfile = _make_dockerfile()
            db.session.commit()
            dockerfile_id = dockerfile.id

        response = dockerfile_client.post(
            f"/dockerfiles/{dockerfile_id}/edit",
            data={
                f"dockerfile-{dockerfile_id}-name": "renamed",
                f"dockerfile-{dockerfile_id}-content": "FROM node:20-slim\n",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            updated = Dockerfile.query.get(dockerfile_id)
            assert updated.name == "renamed"
            assert updated.content == "FROM node:20-slim\n"


class TestDeleteDockerfile:
    def test_deletes_an_unreferenced_dockerfile(self, dockerfile_client, app):
        with app.app_context():
            dockerfile = _make_dockerfile()
            db.session.commit()
            dockerfile_id = dockerfile.id

        response = dockerfile_client.post(f"/dockerfiles/{dockerfile_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert Dockerfile.query.get(dockerfile_id) is None

    def test_blocks_deletion_when_a_builder_references_it(self, dockerfile_client, app):
        with app.app_context():
            dockerfile = _make_dockerfile()
            _make_builder_referencing(dockerfile.id)
            db.session.commit()
            dockerfile_id = dockerfile.id

        response = dockerfile_client.post(f"/dockerfiles/{dockerfile_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"still reference it" in response.data
        with app.app_context():
            assert Dockerfile.query.get(dockerfile_id) is not None

    def test_delete_button_disabled_when_a_builder_references_it(self, dockerfile_client, app):
        with app.app_context():
            dockerfile = _make_dockerfile()
            _make_builder_referencing(dockerfile.id)
            db.session.commit()
            dockerfile_id = dockerfile.id

        response = dockerfile_client.get("/dockerfiles/")
        html = response.data.decode()
        marker = f"delete-dockerfile-modal-{dockerfile_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" in button
        assert "Builder(s) still reference it." in button

    def test_delete_button_enabled_when_unreferenced(self, dockerfile_client, app):
        with app.app_context():
            dockerfile = _make_dockerfile()
            db.session.commit()
            dockerfile_id = dockerfile.id

        response = dockerfile_client.get("/dockerfiles/")
        html = response.data.decode()
        marker = f"delete-dockerfile-modal-{dockerfile_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" not in button


class TestDisableArchiveDockerfile:
    def test_disable_moves_it_off_main_list_onto_archived_page(self, dockerfile_client, app):
        with app.app_context():
            dockerfile = _make_dockerfile()
            _make_builder_referencing(dockerfile.id)
            db.session.commit()
            dockerfile_id = dockerfile.id

        response = dockerfile_client.post(f"/dockerfiles/{dockerfile_id}/disable", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert Dockerfile.query.get(dockerfile_id).is_active is False

        assert f"delete-dockerfile-modal-{dockerfile_id}" not in dockerfile_client.get("/dockerfiles/").data.decode()
        archived_html = dockerfile_client.get("/dockerfiles/archived").data.decode()
        assert "base" in archived_html
        assert f'/dockerfiles/{dockerfile_id}/enable' in archived_html

    def test_enable_restores_it(self, dockerfile_client, app):
        with app.app_context():
            dockerfile = _make_dockerfile()
            dockerfile.is_active = False
            db.session.commit()
            dockerfile_id = dockerfile.id

        response = dockerfile_client.post(f"/dockerfiles/{dockerfile_id}/enable", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert Dockerfile.query.get(dockerfile_id).is_active is True
