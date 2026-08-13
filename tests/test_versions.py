import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    GitSource,
    Permission,
    RegistryTarget,
    Repository,
    Role,
    User,
    Version,
    VersionType,
)

VERSION_PASSWORD = "VersionPass123!"


@pytest.fixture
def version_user(app):
    with app.app_context():
        permissions = [
            Permission(code="version.view", description="View versions"),
            Permission(code="version.manage", description="Manage versions"),
        ]
        db.session.add_all(permissions)
        role = Role(name="VersionAdmin", description="Test version admin role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="version_test",
            password_hash=generate_password_hash(VERSION_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def version_client(client, version_user):
    client.post(
        "/login", data={"username": "version_test", "password": VERSION_PASSWORD}, follow_redirects=True
    )
    return client


@pytest.fixture
def version_type(app):
    with app.app_context():
        vt = VersionType(name="DEV")
        db.session.add(vt)
        db.session.commit()
        return vt.id


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/versions/").status_code == 403

    def test_create_requires_permission(self, noperm_client):
        assert noperm_client.post("/versions/create", data={}).status_code == 403


class TestListVersions:
    def test_empty_state(self, version_client):
        response = version_client.get("/versions/")
        assert response.status_code == 200
        assert b"No versions created yet." in response.data

    def test_lists_a_version_with_no_batches_yet(self, version_client, app, version_type):
        with app.app_context():
            version = Version(name="svc", version_type_id=version_type)
            db.session.add(version)
            db.session.commit()

        response = version_client.get("/versions/")
        assert response.status_code == 200
        assert b"svc" in response.data
        assert b"No batches yet" in response.data

    def test_shows_the_most_recent_batchs_version_string(self, version_client, app, version_type):
        with app.app_context():
            version = Version(name="svc", version_type_id=version_type, major=1)
            db.session.add(version)
            db.session.flush()
            older = BuildBatch(
                version_id=version.id,
                full_version_string="DEV.1.0.0.010101010101",
                bump_type="patch",
                status="success",
            )
            db.session.add(older)
            db.session.flush()
            newer = BuildBatch(
                version_id=version.id,
                full_version_string="DEV.1.1.0.020202020202",
                bump_type="minor",
                status="success",
            )
            db.session.add(newer)
            db.session.commit()

        response = version_client.get("/versions/")
        assert response.status_code == 200
        assert b"DEV.1.1.0.020202020202" in response.data
        assert b"DEV.1.0.0.010101010101" not in response.data

    def test_version_type_field_is_a_text_input_not_a_select(self, version_client):
        response = version_client.get("/versions/")
        assert response.status_code == 200
        assert b'name="create-version-version_type"' in response.data
        assert b'name="create-version-version_type_id"' not in response.data


class TestCreateVersion:
    def test_creates_a_version_with_defaults(self, version_client, app, version_type):
        response = version_client.post(
            "/versions/create",
            data={
                "create-version-name": "backend-service",
                "create-version-version_type": "DEV",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            version = Version.query.filter_by(name="backend-service").first()
            assert version is not None
            assert (version.major, version.minor, version.patch) == (0, 0, 0)
            assert version.version_type.name == "DEV"

    def test_creates_a_version_with_a_custom_starting_number(self, version_client, app, version_type):
        version_client.post(
            "/versions/create",
            data={
                "create-version-name": "legacy-service",
                "create-version-version_type": "DEV",
                "create-version-major": "3",
                "create-version-minor": "5",
                "create-version-patch": "12",
            },
        )
        with app.app_context():
            version = Version.query.filter_by(name="legacy-service").first()
            assert (version.major, version.minor, version.patch) == (3, 5, 12)

    def test_duplicate_name_is_rejected(self, version_client, app, version_type):
        with app.app_context():
            db.session.add(Version(name="dup", version_type_id=version_type))
            db.session.commit()

        version_client.post(
            "/versions/create",
            data={"create-version-name": "dup", "create-version-version_type": "DEV"},
        )
        with app.app_context():
            assert Version.query.filter_by(name="dup").count() == 1

    def test_typing_an_unseen_type_name_creates_a_new_version_type(self, version_client, app):
        response = version_client.post(
            "/versions/create",
            data={"create-version-name": "new-svc", "create-version-version_type": "STAGING"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            version_type = VersionType.query.filter_by(name="STAGING").first()
            assert version_type is not None
            version = Version.query.filter_by(name="new-svc").first()
            assert version.version_type_id == version_type.id

    def test_typing_an_existing_type_name_case_insensitively_reuses_it(
        self, version_client, app, version_type
    ):
        version_client.post(
            "/versions/create",
            data={"create-version-name": "reuse-svc", "create-version-version_type": "dev"},
        )
        with app.app_context():
            assert VersionType.query.filter(VersionType.name.ilike("dev")).count() == 1
            version = Version.query.filter_by(name="reuse-svc").first()
            assert version.version_type_id == version_type

    def test_blank_version_type_does_not_create(self, version_client, app):
        version_client.post(
            "/versions/create", data={"create-version-name": "no-type", "create-version-version_type": ""}
        )
        with app.app_context():
            assert Version.query.filter_by(name="no-type").first() is None


class TestEditVersion:
    def test_edits_name_and_numbers(self, version_client, app, version_type):
        with app.app_context():
            version = Version(name="old-name", version_type_id=version_type)
            db.session.add(version)
            db.session.commit()
            version_id = version.id

        version_client.post(
            f"/versions/{version_id}/edit",
            data={
                f"version-{version_id}-name": "new-name",
                f"version-{version_id}-version_type": "DEV",
                f"version-{version_id}-major": "2",
                f"version-{version_id}-minor": "0",
                f"version-{version_id}-patch": "0",
            },
        )
        with app.app_context():
            version = Version.query.get(version_id)
            assert version.name == "new-name"
            assert (version.major, version.minor, version.patch) == (2, 0, 0)

    def test_changing_the_type_name_to_a_new_one_creates_it(self, version_client, app, version_type):
        with app.app_context():
            version = Version(name="svc", version_type_id=version_type)
            db.session.add(version)
            db.session.commit()
            version_id = version.id

        version_client.post(
            f"/versions/{version_id}/edit",
            data={f"version-{version_id}-name": "svc", f"version-{version_id}-version_type": "PROD"},
        )
        with app.app_context():
            version = Version.query.get(version_id)
            assert version.version_type.name == "PROD"

    def test_renaming_to_an_existing_name_is_rejected(self, version_client, app, version_type):
        with app.app_context():
            db.session.add(Version(name="taken", version_type_id=version_type))
            other = Version(name="mine", version_type_id=version_type)
            db.session.add(other)
            db.session.commit()
            other_id = other.id

        version_client.post(
            f"/versions/{other_id}/edit",
            data={
                f"version-{other_id}-name": "taken",
                f"version-{other_id}-version_type": "DEV",
            },
        )
        with app.app_context():
            assert Version.query.get(other_id).name == "mine"


class TestDeleteVersion:
    def test_delete_requires_permission(self, noperm_client, app, version_type):
        with app.app_context():
            version = Version(name="svc", version_type_id=version_type)
            db.session.add(version)
            db.session.commit()
            version_id = version.id

        response = noperm_client.post(f"/versions/{version_id}/delete")
        assert response.status_code == 403

    def test_deletes_a_version_with_no_builders_attached(self, version_client, app, version_type):
        with app.app_context():
            version = Version(name="svc", version_type_id=version_type)
            db.session.add(version)
            db.session.commit()
            version_id = version.id

        response = version_client.post(f"/versions/{version_id}/delete", follow_redirects=True)
        assert response.status_code == 200

        with app.app_context():
            assert Version.query.get(version_id) is None

    def test_blocked_when_a_builder_is_still_attached(self, version_client, app, version_type):
        with app.app_context():
            version = Version(name="svc", version_type_id=version_type)
            db.session.add(version)
            db.session.flush()

            git_source = GitSource(name="conn", provider_type="github", encrypted_token="x")
            db.session.add(git_source)
            db.session.flush()
            repository = Repository(
                git_source_id=git_source.id, full_name="org/repo", local_path="/tmp/repo", status="ready"
            )
            db.session.add(repository)
            registry_target = RegistryTarget(name="reg", provider_type="dockerhub")
            db.session.add(registry_target)
            db.session.flush()
            db.session.add(
                Builder(
                    name="b1",
                    version_id=version.id,
                    repository_id=repository.id,
                    registry_target_id=registry_target.id,
                    dockerfile_path="Dockerfile",
                )
            )
            db.session.commit()
            version_id = version.id

        response = version_client.post(f"/versions/{version_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"still reference it" in response.data

        with app.app_context():
            assert Version.query.get(version_id) is not None


class TestLinkedVersions:
    def test_linking_on_create_is_one_directional(self, version_client, app, version_type):
        with app.app_context():
            dev = Version(name="DEV-svc", version_type_id=version_type)
            db.session.add(dev)
            db.session.commit()
            dev_id = dev.id

        response = version_client.post(
            "/versions/create",
            data={
                "create-version-name": "QAS-svc",
                "create-version-version_type": "DEV",
                "create-version-linked_version_ids": [str(dev_id)],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            qas = Version.query.filter_by(name="QAS-svc").first()
            dev = Version.query.get(dev_id)
            assert [v.name for v in qas.linked_versions] == ["DEV-svc"]
            # One-directional: DEV's own picker does NOT show QAS as linked —
            # only QAS's own form granted this, and only in that direction.
            assert dev.linked_versions == []

    def test_editing_links_and_unlinks_only_this_versions_own_direction(
        self, version_client, app, version_type
    ):
        with app.app_context():
            dev = Version(name="DEV-svc", version_type_id=version_type)
            qas = Version(name="QAS-svc", version_type_id=version_type)
            db.session.add_all([dev, qas])
            db.session.commit()
            dev_id, qas_id = dev.id, qas.id

        version_client.post(
            f"/versions/{qas_id}/edit",
            data={
                f"version-{qas_id}-name": "QAS-svc",
                f"version-{qas_id}-version_type": "DEV",
                f"version-{qas_id}-linked_version_ids": [str(dev_id)],
            },
        )
        with app.app_context():
            assert [v.name for v in Version.query.get(qas_id).linked_versions] == ["DEV-svc"]
            # DEV's own outgoing links are untouched by QAS's edit.
            assert Version.query.get(dev_id).linked_versions == []

        # Unlinking from QAS's side only ever affects QAS's own direction.
        version_client.post(
            f"/versions/{qas_id}/edit",
            data={
                f"version-{qas_id}-name": "QAS-svc",
                f"version-{qas_id}-version_type": "DEV",
            },
        )
        with app.app_context():
            assert Version.query.get(qas_id).linked_versions == []
            assert Version.query.get(dev_id).linked_versions == []

    def test_linking_both_directions_requires_editing_both_versions(
        self, version_client, app, version_type
    ):
        with app.app_context():
            dev = Version(name="DEV-svc", version_type_id=version_type)
            qas = Version(name="QAS-svc", version_type_id=version_type)
            db.session.add_all([dev, qas])
            db.session.commit()
            dev_id, qas_id = dev.id, qas.id

        version_client.post(
            f"/versions/{qas_id}/edit",
            data={
                f"version-{qas_id}-name": "QAS-svc",
                f"version-{qas_id}-version_type": "DEV",
                f"version-{qas_id}-linked_version_ids": [str(dev_id)],
            },
        )
        version_client.post(
            f"/versions/{dev_id}/edit",
            data={
                f"version-{dev_id}-name": "DEV-svc",
                f"version-{dev_id}-version_type": "DEV",
                f"version-{dev_id}-linked_version_ids": [str(qas_id)],
            },
        )
        with app.app_context():
            assert [v.name for v in Version.query.get(qas_id).linked_versions] == ["DEV-svc"]
            assert [v.name for v in Version.query.get(dev_id).linked_versions] == ["QAS-svc"]

    def test_a_version_cannot_link_to_itself(self, version_client, app, version_type):
        with app.app_context():
            version = Version(name="solo-svc", version_type_id=version_type)
            db.session.add(version)
            db.session.commit()
            version_id = version.id

        version_client.post(
            f"/versions/{version_id}/edit",
            data={
                f"version-{version_id}-name": "solo-svc",
                f"version-{version_id}-version_type": "DEV",
                f"version-{version_id}-linked_version_ids": [str(version_id)],
            },
        )
        with app.app_context():
            assert Version.query.get(version_id).linked_versions == []
