import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Builder,
    GitSource,
    Permission,
    RegistryTarget,
    Repository,
    Role,
    User,
    Version,
    VersionType,
)
from app.services.registry.dockerhub import DockerHubProvider
from app.utils.crypto import decrypt

REGISTRY_PASSWORD = "RegistryPass123!"


@pytest.fixture
def registry_user(app):
    with app.app_context():
        permission = Permission(code="registry.manage", description="Manage registries")
        db.session.add(permission)
        role = Role(name="RegistryAdmin", description="Test registry admin role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="registry_test",
            password_hash=generate_password_hash(REGISTRY_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def registry_client(client, registry_user):
    client.post(
        "/login", data={"username": "registry_test", "password": REGISTRY_PASSWORD}, follow_redirects=True
    )
    return client


def _make_builder_referencing(registry_target_id):
    git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
    db.session.add(git_source)
    db.session.flush()
    repository = Repository(
        git_source_id=git_source.id, full_name="org/repo", local_path="/tmp/x", status="ready"
    )
    db.session.add(repository)
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
        registry_target_id=registry_target_id,
        dockerfile_path="Dockerfile",
    )
    db.session.add(builder)
    db.session.flush()
    return builder


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/registries/").status_code == 403

    def test_create_requires_permission(self, noperm_client):
        assert noperm_client.post("/registries/create", data={}).status_code == 403


class TestCreateRegistry:
    def test_successful_creation_encrypts_the_token(self, registry_client, app, monkeypatch):
        monkeypatch.setattr(DockerHubProvider, "validate_credentials", lambda self: True)

        response = registry_client.post(
            "/registries/create",
            data={
                "create-registry-name": "main-dockerhub",
                "create-registry-provider_type": "dockerhub",
                "create-registry-username": "someuser",
                "create-registry-token": "super-secret",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            target = RegistryTarget.query.filter_by(name="main-dockerhub").first()
            assert target is not None
            assert target.encrypted_token != "super-secret"
            assert decrypt(target.encrypted_token) == "super-secret"

    def test_failed_validation_does_not_create_the_target(self, registry_client, app, monkeypatch):
        def _raise(self):
            raise RuntimeError("bad credentials")

        monkeypatch.setattr(DockerHubProvider, "validate_credentials", _raise)

        response = registry_client.post(
            "/registries/create",
            data={
                "create-registry-name": "bad-dockerhub",
                "create-registry-provider_type": "dockerhub",
                "create-registry-username": "someuser",
                "create-registry-token": "wrong-token",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"bad credentials" in response.data

        with app.app_context():
            assert RegistryTarget.query.filter_by(name="bad-dockerhub").first() is None

    def test_missing_token_is_rejected(self, registry_client, app):
        registry_client.post(
            "/registries/create",
            data={
                "create-registry-name": "no-token",
                "create-registry-provider_type": "dockerhub",
                "create-registry-username": "someuser",
            },
        )
        with app.app_context():
            assert RegistryTarget.query.filter_by(name="no-token").first() is None

    def test_non_dockerhub_without_registry_url_is_rejected(self, registry_client, app):
        registry_client.post(
            "/registries/create",
            data={
                "create-registry-name": "harbor-no-url",
                "create-registry-provider_type": "harbor",
                "create-registry-username": "someuser",
                "create-registry-token": "sometoken",
            },
        )
        with app.app_context():
            assert RegistryTarget.query.filter_by(name="harbor-no-url").first() is None

    def test_unimplemented_provider_type_skips_validation_and_still_saves(self, registry_client, app):
        response = registry_client.post(
            "/registries/create",
            data={
                "create-registry-name": "custom-future",
                "create-registry-provider_type": "custom",
                "create-registry-username": "someuser",
                "create-registry-token": "sometoken",
                "create-registry-registry_url": "registry.example.com",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            target = RegistryTarget.query.filter_by(name="custom-future").first()
            assert target is not None
            assert target.provider_type == "custom"


class TestEditRegistry:
    def test_editing_without_a_new_token_keeps_the_existing_one(self, registry_client, app, monkeypatch):
        monkeypatch.setattr(DockerHubProvider, "validate_credentials", lambda self: True)
        with app.app_context():
            from app.utils.crypto import encrypt

            target = RegistryTarget(
                name="reg", provider_type="dockerhub", username="olduser", encrypted_token=encrypt("original")
            )
            db.session.add(target)
            db.session.commit()
            target_id = target.id

        registry_client.post(
            f"/registries/{target_id}/edit",
            data={
                f"registry-{target_id}-name": "reg",
                f"registry-{target_id}-provider_type": "dockerhub",
                f"registry-{target_id}-username": "olduser",
                f"registry-{target_id}-token": "",
            },
        )

        with app.app_context():
            target = RegistryTarget.query.get(target_id)
            assert decrypt(target.encrypted_token) == "original"

    def test_editing_with_a_new_token_replaces_it(self, registry_client, app, monkeypatch):
        monkeypatch.setattr(DockerHubProvider, "validate_credentials", lambda self: True)
        with app.app_context():
            from app.utils.crypto import encrypt

            target = RegistryTarget(
                name="reg", provider_type="dockerhub", username="olduser", encrypted_token=encrypt("original")
            )
            db.session.add(target)
            db.session.commit()
            target_id = target.id

        registry_client.post(
            f"/registries/{target_id}/edit",
            data={
                f"registry-{target_id}-name": "reg",
                f"registry-{target_id}-provider_type": "dockerhub",
                f"registry-{target_id}-username": "olduser",
                f"registry-{target_id}-token": "new-token",
            },
        )

        with app.app_context():
            target = RegistryTarget.query.get(target_id)
            assert decrypt(target.encrypted_token) == "new-token"

    def test_the_edit_form_never_shows_the_existing_token(self, registry_client, app):
        with app.app_context():
            from app.utils.crypto import encrypt

            target = RegistryTarget(
                name="reg", provider_type="dockerhub", username="olduser", encrypted_token=encrypt("super-secret")
            )
            db.session.add(target)
            db.session.commit()

        response = registry_client.get("/registries/")
        assert b"super-secret" not in response.data


class TestDeleteRegistry:
    def test_deletes_when_unreferenced(self, registry_client, app):
        with app.app_context():
            target = RegistryTarget(name="reg", provider_type="dockerhub")
            db.session.add(target)
            db.session.commit()
            target_id = target.id

        response = registry_client.post(f"/registries/{target_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert RegistryTarget.query.get(target_id) is None

    def test_blocked_when_a_builder_still_references_it(self, registry_client, app):
        with app.app_context():
            target = RegistryTarget(name="reg", provider_type="dockerhub")
            db.session.add(target)
            db.session.flush()
            _make_builder_referencing(target.id)
            db.session.commit()
            target_id = target.id

        response = registry_client.post(f"/registries/{target_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"still reference it" in response.data
        with app.app_context():
            assert RegistryTarget.query.get(target_id) is not None

    def test_delete_button_disabled_when_a_builder_still_references_it(self, registry_client, app):
        with app.app_context():
            target = RegistryTarget(name="reg", provider_type="dockerhub")
            db.session.add(target)
            db.session.flush()
            _make_builder_referencing(target.id)
            db.session.commit()
            target_id = target.id

        response = registry_client.get("/registries/")
        html = response.data.decode()
        marker = f"delete-registry-modal-{target_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" in button
        assert "Builder(s) still reference it." in button

    def test_delete_button_enabled_when_unreferenced(self, registry_client, app):
        with app.app_context():
            target = RegistryTarget(name="reg", provider_type="dockerhub")
            db.session.add(target)
            db.session.commit()
            target_id = target.id

        response = registry_client.get("/registries/")
        html = response.data.decode()
        marker = f"delete-registry-modal-{target_id}"
        button = html[html.index(marker) : html.index(marker) + 400]
        assert "disabled" not in button


class TestDisableArchiveRegistry:
    def test_disable_moves_it_off_main_list_onto_archived_page(self, registry_client, app):
        with app.app_context():
            target = RegistryTarget(name="reg", provider_type="dockerhub")
            db.session.add(target)
            db.session.flush()
            _make_builder_referencing(target.id)
            db.session.commit()
            target_id = target.id

        response = registry_client.post(f"/registries/{target_id}/disable", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert RegistryTarget.query.get(target_id).is_active is False

        assert f"delete-registry-modal-{target_id}" not in registry_client.get("/registries/").data.decode()
        archived_html = registry_client.get("/registries/archived").data.decode()
        assert "reg" in archived_html
        assert f'/registries/{target_id}/enable' in archived_html

    def test_enable_restores_it(self, registry_client, app):
        with app.app_context():
            target = RegistryTarget(name="reg", provider_type="dockerhub", is_active=False)
            db.session.add(target)
            db.session.commit()
            target_id = target.id

        response = registry_client.post(f"/registries/{target_id}/enable", follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            assert RegistryTarget.query.get(target_id).is_active is True
