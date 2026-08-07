import os

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    GitSource,
    ImageBuild,
    Permission,
    Repository,
    RegistryTarget,
    Role,
    User,
    Version,
    VersionType,
)
from app.utils.crypto import decrypt, encrypt

GIT_PASSWORD = "GitPass123!"


class FakeProvider:
    def __init__(self, repos=None, clone_branch="main", clone_error=None, sync_error=None):
        self.repos = repos or []
        self.clone_branch = clone_branch
        self.clone_error = clone_error
        self.sync_error = sync_error
        self.synced = []
        self.synced_repo_names = []

    def list_repos(self):
        return self.repos

    def clone_repo(self, repo_name, local_path):
        if self.clone_error:
            raise RuntimeError(self.clone_error)
        os.makedirs(local_path, exist_ok=True)
        return self.clone_branch

    def sync_repo(self, local_path, branch, repo_name=None):
        if self.sync_error:
            raise RuntimeError(self.sync_error)
        self.synced.append((local_path, branch))
        self.synced_repo_names.append(repo_name)


@pytest.fixture
def git_user(app):
    with app.app_context():
        permission = Permission(code="gitsource.manage", description="Manage git sources")
        db.session.add(permission)
        role = Role(name="GitAdmin", description="Test git admin role")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="git_test",
            password_hash=generate_password_hash(GIT_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def git_client(client, git_user):
    client.post("/login", data={"username": "git_test", "password": GIT_PASSWORD}, follow_redirects=True)
    return client


def _make_builder_referencing(repo_id, batch_status=None):
    """Constructs the minimal Version/RegistryTarget/Builder chain (and
    optionally a running ImageBuild in a BuildBatch) needed to exercise the
    "still referenced" / "build in progress" guard rails.
    """
    version_type = VersionType(name="DEV")
    db.session.add(version_type)
    db.session.flush()
    version = Version(name="svc", version_type_id=version_type.id)
    db.session.add(version)
    registry_target = RegistryTarget(name="reg", provider_type="dockerhub")
    db.session.add(registry_target)
    db.session.flush()
    builder = Builder(
        name="b1",
        version_id=version.id,
        repository_id=repo_id,
        registry_target_id=registry_target.id,
        dockerfile_path="Dockerfile",
    )
    db.session.add(builder)
    db.session.flush()

    if batch_status:
        batch = BuildBatch(
            version_id=version.id,
            full_version_string="DEV.0.0.1.010101010101",
            bump_type="patch",
            status=batch_status,
        )
        db.session.add(batch)
        db.session.flush()
        image_build = ImageBuild(
            batch_id=batch.id, builder_id=builder.id, branch_used="main", status=batch_status
        )
        db.session.add(image_build)
        db.session.flush()

    return builder


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/github/").status_code == 403

    def test_create_connection_requires_permission(self, noperm_client):
        assert noperm_client.post("/github/connections/create", data={}).status_code == 403

    def test_edit_connection_requires_permission(self, noperm_client, app):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.commit()
            source_id = source.id

        response = noperm_client.post(f"/github/connections/{source_id}/edit", data={})
        assert response.status_code == 403

    def test_delete_connection_requires_permission(self, noperm_client, app):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.commit()
            source_id = source.id

        response = noperm_client.post(f"/github/connections/{source_id}/delete")
        assert response.status_code == 403


class TestCreateConnection:
    def test_creates_a_git_source_with_encrypted_token(self, git_client, app):
        response = git_client.post(
            "/github/connections/create",
            data={
                "create-connection-name": "org-main-token",
                "create-connection-token": "ghp_supersecret",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            source = GitSource.query.filter_by(name="org-main-token").first()
            assert source is not None
            assert source.encrypted_token != "ghp_supersecret"
            assert decrypt(source.encrypted_token) == "ghp_supersecret"

    def test_missing_token_does_not_create(self, git_client, app):
        git_client.post(
            "/github/connections/create",
            data={"create-connection-name": "missing-token"},
        )
        with app.app_context():
            assert GitSource.query.filter_by(name="missing-token").first() is None


class TestIndexRepoListing:
    def test_shows_unregistered_repos_and_hides_already_registered_ones(
        self, git_client, app, monkeypatch
    ):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.flush()
            repo = Repository(
                git_source_id=source.id,
                full_name="org/already-registered",
                local_path="/tmp/whatever",
                status="ready",
            )
            db.session.add(repo)
            db.session.commit()

        fake = FakeProvider(
            repos=[
                {"full_name": "org/already-registered", "default_branch": "main", "private": False},
                {"full_name": "org/new-repo", "default_branch": "main", "private": True},
            ]
        )
        monkeypatch.setattr("app.blueprints.git_sources.routes._provider_for", lambda source: fake)

        response = git_client.get("/github/")
        assert response.status_code == 200
        # "already-registered" still shows up in the Registered table (and its
        # Remove-confirmation modal text) — it must NOT also have a Register
        # button, which is what the hidden full_name input on that form implies.
        assert b'name="full_name" value="org/already-registered"' not in response.data
        assert b'name="full_name" value="org/new-repo"' in response.data

    def test_list_error_is_shown_without_crashing_the_page(self, git_client, app, monkeypatch):
        with app.app_context():
            source = GitSource(name="broken-conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.commit()

        class BrokenProvider:
            def list_repos(self):
                raise RuntimeError("bad credentials")

        monkeypatch.setattr("app.blueprints.git_sources.routes._provider_for", lambda source: BrokenProvider())

        response = git_client.get("/github/")
        assert response.status_code == 200
        assert b"bad credentials" in response.data

        with app.app_context():
            from app.models import ErrorLog

            error_log = ErrorLog.query.filter_by(source="git_sources.list_repos").first()
            assert error_log is not None
            assert "bad credentials" in error_log.message


class TestRegisterRepo:
    def test_successful_registration_creates_a_ready_repository(self, git_client, app, monkeypatch, tmp_path):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.commit()
            source_id = source.id

        app.config["REPO_CLONE_ROOT"] = str(tmp_path)
        fake = FakeProvider(clone_branch="develop")
        monkeypatch.setattr("app.blueprints.git_sources.routes._provider_for", lambda source: fake)

        response = git_client.post(
            "/github/repos/register",
            data={"git_source_id": str(source_id), "full_name": "org/repo"},
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            repo = Repository.query.filter_by(full_name="org/repo").first()
            assert repo is not None
            assert repo.status == "ready"
            assert repo.default_branch == "develop"
            assert repo.local_path.endswith(str(repo.id))
            assert repo.last_synced_at is not None

    def test_clone_failure_marks_repository_as_error(self, git_client, app, monkeypatch, tmp_path):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.commit()
            source_id = source.id

        app.config["REPO_CLONE_ROOT"] = str(tmp_path)
        fake = FakeProvider(clone_error="boom")
        monkeypatch.setattr("app.blueprints.git_sources.routes._provider_for", lambda source: fake)

        git_client.post(
            "/github/repos/register",
            data={"git_source_id": str(source_id), "full_name": "org/repo"},
        )

        with app.app_context():
            repo = Repository.query.filter_by(full_name="org/repo").first()
            assert repo is not None
            assert repo.status == "error"

            from app.models import ErrorLog

            error_log = ErrorLog.query.filter_by(source="git_sources.register_repo").first()
            assert error_log is not None
            assert "boom" in error_log.message

    def test_registering_an_already_registered_repo_is_rejected(self, git_client, app, tmp_path):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.flush()
            existing = Repository(
                git_source_id=source.id,
                full_name="org/repo",
                local_path=str(tmp_path),
                status="ready",
            )
            db.session.add(existing)
            db.session.commit()
            source_id = source.id

        response = git_client.post(
            "/github/repos/register",
            data={"git_source_id": str(source_id), "full_name": "org/repo"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert Repository.query.filter_by(full_name="org/repo").count() == 1


class TestResyncRepo:
    def test_successful_resync_updates_last_synced_at(self, git_client, app, monkeypatch, tmp_path):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.flush()
            repo = Repository(
                git_source_id=source.id,
                full_name="org/repo",
                local_path=str(tmp_path),
                status="ready",
                default_branch="main",
            )
            db.session.add(repo)
            db.session.commit()
            repo_id = repo.id

        fake = FakeProvider()
        monkeypatch.setattr("app.blueprints.git_sources.routes._provider_for", lambda source: fake)

        response = git_client.post(f"/github/repos/{repo_id}/resync", follow_redirects=True)
        assert response.status_code == 200

        with app.app_context():
            repo = Repository.query.get(repo_id)
            assert repo.last_synced_at is not None
        assert fake.synced == [(str(tmp_path), "main")]

    def test_passes_the_repo_name_so_the_provider_can_self_heal_a_missing_clone(
        self, git_client, app, monkeypatch, tmp_path
    ):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.flush()
            repo = Repository(
                git_source_id=source.id,
                full_name="org/repo",
                local_path=str(tmp_path / "does-not-exist"),
                status="ready",
                default_branch="main",
            )
            db.session.add(repo)
            db.session.commit()
            repo_id = repo.id

        fake = FakeProvider()
        monkeypatch.setattr("app.blueprints.git_sources.routes._provider_for", lambda source: fake)

        response = git_client.post(f"/github/repos/{repo_id}/resync", follow_redirects=True)
        assert response.status_code == 200

        assert fake.synced_repo_names == ["org/repo"]

    def test_blocked_while_a_build_is_running(self, git_client, app, tmp_path):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.flush()
            repo = Repository(
                git_source_id=source.id,
                full_name="org/repo",
                local_path=str(tmp_path),
                status="ready",
                default_branch="main",
            )
            db.session.add(repo)
            db.session.flush()
            _make_builder_referencing(repo.id, batch_status="running")
            db.session.commit()
            repo_id = repo.id

        response = git_client.post(f"/github/repos/{repo_id}/resync", follow_redirects=True)
        assert response.status_code == 200
        assert b"currently running" in response.data


class TestRemoveRepo:
    def test_removes_registration_and_deletes_local_clone(self, git_client, app, tmp_path):
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / "file.txt").write_text("x")

        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.flush()
            repo = Repository(
                git_source_id=source.id,
                full_name="org/repo",
                local_path=str(clone_dir),
                status="ready",
            )
            db.session.add(repo)
            db.session.commit()
            repo_id = repo.id

        response = git_client.post(f"/github/repos/{repo_id}/remove", follow_redirects=True)
        assert response.status_code == 200

        with app.app_context():
            assert Repository.query.get(repo_id) is None
        assert not clone_dir.exists()

    def test_blocked_when_a_builder_still_references_it(self, git_client, app, tmp_path):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.flush()
            repo = Repository(
                git_source_id=source.id,
                full_name="org/repo",
                local_path=str(tmp_path),
                status="ready",
            )
            db.session.add(repo)
            db.session.flush()
            _make_builder_referencing(repo.id)
            db.session.commit()
            repo_id = repo.id

        response = git_client.post(f"/github/repos/{repo_id}/remove", follow_redirects=True)
        assert response.status_code == 200
        assert b"still reference it" in response.data

        with app.app_context():
            assert Repository.query.get(repo_id) is not None


class TestEditConnection:
    def test_renames_the_connection(self, git_client, app):
        with app.app_context():
            source = GitSource(name="old-name", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.commit()
            source_id = source.id

        response = git_client.post(
            f"/github/connections/{source_id}/edit",
            data={f"connection-{source_id}-name": "new-name"},
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            assert GitSource.query.get(source_id).name == "new-name"

    def test_blank_token_keeps_the_existing_one(self, git_client, app):
        with app.app_context():
            source = GitSource(
                name="conn", provider_type="github", encrypted_token=encrypt("original-token")
            )
            db.session.add(source)
            db.session.commit()
            source_id = source.id

        git_client.post(
            f"/github/connections/{source_id}/edit",
            data={f"connection-{source_id}-name": "conn"},
        )

        with app.app_context():
            source = GitSource.query.get(source_id)
            assert decrypt(source.encrypted_token) == "original-token"

    def test_a_new_token_replaces_the_old_one(self, git_client, app):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token=encrypt("old-token"))
            db.session.add(source)
            db.session.commit()
            source_id = source.id

        git_client.post(
            f"/github/connections/{source_id}/edit",
            data={
                f"connection-{source_id}-name": "conn",
                f"connection-{source_id}-token": "new-token",
            },
        )

        with app.app_context():
            source = GitSource.query.get(source_id)
            assert decrypt(source.encrypted_token) == "new-token"


class TestDeleteConnection:
    def test_deletes_a_connection_with_no_registered_repos(self, git_client, app):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.commit()
            source_id = source.id

        response = git_client.post(f"/github/connections/{source_id}/delete", follow_redirects=True)
        assert response.status_code == 200

        with app.app_context():
            assert GitSource.query.get(source_id) is None

    def test_blocked_when_a_repository_is_still_registered(self, git_client, app, tmp_path):
        with app.app_context():
            source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(source)
            db.session.flush()
            db.session.add(
                Repository(
                    git_source_id=source.id,
                    full_name="org/repo",
                    local_path=str(tmp_path),
                    status="ready",
                )
            )
            db.session.commit()
            source_id = source.id

        response = git_client.post(f"/github/connections/{source_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"still registered under it" in response.data

        with app.app_context():
            assert GitSource.query.get(source_id) is not None
