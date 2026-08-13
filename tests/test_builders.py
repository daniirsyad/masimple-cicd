import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Builder,
    BuildBatch,
    ChangeType,
    GitSource,
    ImageBuild,
    Object,
    Permission,
    RegistryTarget,
    Repository,
    Role,
    User,
    Version,
    VersionDocumentation,
    VersionType,
)

BUILDER_PASSWORD = "BuilderPass123!"


class FakeProvider:
    def __init__(self, branches=None, dockerfiles=None):
        self.branches = branches if branches is not None else ["main", "develop"]
        self.dockerfiles = dockerfiles if dockerfiles is not None else ["Dockerfile"]

    def list_branches(self, local_path):
        return self.branches

    def list_files(self, local_path):
        return self.dockerfiles


@pytest.fixture
def builder_user(app):
    with app.app_context():
        permissions = [
            Permission(code="builder.view", description="View builders"),
            Permission(code="builder.manage", description="Manage builders"),
            Permission(code="builder.build", description="Trigger builds"),
            Permission(code="image.view", description="View images"),
        ]
        db.session.add_all(permissions)
        role = Role(name="BuilderAdmin", description="Test builder admin role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="builder_test",
            password_hash=generate_password_hash(BUILDER_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def builder_client(client, builder_user):
    client.post(
        "/login", data={"username": "builder_test", "password": BUILDER_PASSWORD}, follow_redirects=True
    )
    return client


LIMITED_PASSWORD = "LimitedPass123!"


@pytest.fixture
def limited_role(app):
    """A role with builder.view + builder.build (and image.view, needed to
    land on the post-build redirect target) but not builder.manage — the
    "limited role" that per-Builder allowed_roles is meant to scope."""
    with app.app_context():
        codes = ["builder.view", "builder.build", "image.view"]
        permissions = []
        for code in codes:
            permission = Permission.query.filter_by(code=code).first()
            if permission is None:
                permission = Permission(code=code, description=code)
                db.session.add(permission)
                db.session.flush()
            permissions.append(permission)
        role = Role(name="LimitedBuilder", description="Restricted builder role")
        role.permissions = permissions
        db.session.add(role)
        db.session.commit()
        return role.id


@pytest.fixture
def limited_client(client, app, limited_role):
    with app.app_context():
        user = User(
            username="limited_test",
            password_hash=generate_password_hash(LIMITED_PASSWORD),
            is_active=True,
            role_id=limited_role,
        )
        db.session.add(user)
        db.session.commit()
    client.post(
        "/login", data={"username": "limited_test", "password": LIMITED_PASSWORD}, follow_redirects=True
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
        db.session.commit()
        return {
            "repository_id": repository.id,
            "registry_target_id": registry_target.id,
            "version_id": version.id,
        }


@pytest.fixture(autouse=True)
def _fake_git_provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr("app.blueprints.builders.routes.provider_for_git_source", lambda source: fake)
    return fake


def _make_builder(base_entities, name="b1", default_branch="main"):
    builder = Builder(
        name=name,
        version_id=base_entities["version_id"],
        repository_id=base_entities["repository_id"],
        default_branch=default_branch,
        dockerfile_path="Dockerfile",
        registry_target_id=base_entities["registry_target_id"],
    )
    db.session.add(builder)
    db.session.flush()
    return builder


def _make_change_type(name="Bug Fix"):
    change_type = ChangeType(name=name)
    db.session.add(change_type)
    db.session.flush()
    return change_type


class TestPermissionGating:
    def test_index_requires_permission(self, noperm_client):
        assert noperm_client.get("/builders/").status_code == 403

    def test_create_requires_permission(self, noperm_client):
        assert noperm_client.post("/builders/create", data={}).status_code == 403

    def test_build_requires_permission(self, noperm_client):
        assert noperm_client.post("/builders/build", data={}).status_code == 403

    def test_delete_requires_permission(self, noperm_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id
        assert noperm_client.post(f"/builders/{builder_id}/delete").status_code == 403


class TestListBuilders:
    def test_empty_state(self, builder_client):
        response = builder_client.get("/builders/")
        assert response.status_code == 200
        assert b"No builders created yet." in response.data

    def test_lists_a_builder(self, builder_client, app, base_entities):
        with app.app_context():
            _make_builder(base_entities)
            db.session.commit()

        response = builder_client.get("/builders/")
        assert response.status_code == 200
        assert b"b1" in response.data
        assert b"org/repo" in response.data
        assert b"Never built" in response.data

    def test_filters_by_version(self, builder_client, app, base_entities):
        with app.app_context():
            _make_builder(base_entities, name="b1")
            other_version_type = VersionType(name="STAGING")
            db.session.add(other_version_type)
            db.session.flush()
            other_version = Version(name="other-svc", version_type_id=other_version_type.id)
            db.session.add(other_version)
            db.session.flush()
            other_builder = Builder(
                name="b2",
                version_id=other_version.id,
                repository_id=base_entities["repository_id"],
                default_branch="main",
                dockerfile_path="Dockerfile",
                registry_target_id=base_entities["registry_target_id"],
            )
            db.session.add(other_builder)
            db.session.commit()
            target_version_id = base_entities["version_id"]

        response = builder_client.get(f"/builders/?version_id={target_version_id}")
        assert response.status_code == 200
        assert b'data-builder-name="b1"' in response.data
        assert b'data-builder-name="b2"' not in response.data

    def test_groups_builders_by_group_name(self, builder_client, app, base_entities):
        with app.app_context():
            b1 = _make_builder(base_entities, name="b1")
            b1.group_name = "frontend"
            b2 = Builder(
                name="b2",
                version_id=base_entities["version_id"],
                repository_id=base_entities["repository_id"],
                default_branch="main",
                group_name="frontend",
                dockerfile_path="Dockerfile",
                registry_target_id=base_entities["registry_target_id"],
            )
            db.session.add(b2)
            db.session.commit()

        response = builder_client.get("/builders/")
        assert response.status_code == 200
        html = response.data.decode()
        assert 'data-group-name="frontend"' in html
        assert html.count('class="build-group-btn') == 1
        # Grouped builders are only ever built via the group action — no
        # per-row Build button, unlike ungrouped rows (see the next test).
        assert html.count('class="build-one-btn') == 0
        # ...but the row still carries the builder's identity as data
        # attributes, since builders.js's "Build Group" handler reads them
        # off the row itself rather than off a (now-absent) button.
        assert html.count('class="builder-row"') == 2
        assert 'data-builder-name="b1"' in html
        assert 'data-builder-name="b2"' in html

    def test_ungrouped_builders_have_no_group_section_or_group_build_button(
        self, builder_client, app, base_entities
    ):
        with app.app_context():
            _make_builder(base_entities, name="solo")
            db.session.commit()

        response = builder_client.get("/builders/")
        html = response.data.decode()
        assert "Ungrouped" in html
        assert '"solo"' in html or "solo" in html
        assert 'class="build-group-btn' not in html
        assert html.count('class="build-one-btn') == 1

    def test_a_group_can_span_multiple_versions(self, builder_client, app, base_entities):
        """Group membership is independent of Version — the same-Version
        constraint is only enforced when a batch is actually triggered
        (POST /builders/build), not at grouping/display time.
        """
        with app.app_context():
            b1 = _make_builder(base_entities, name="b1")
            b1.group_name = "cross-version"
            other_version_type = VersionType(name="STAGING")
            db.session.add(other_version_type)
            db.session.flush()
            other_version = Version(name="other-svc", version_type_id=other_version_type.id)
            db.session.add(other_version)
            db.session.flush()
            b2 = Builder(
                name="b2",
                version_id=other_version.id,
                repository_id=base_entities["repository_id"],
                default_branch="main",
                group_name="cross-version",
                dockerfile_path="Dockerfile",
                registry_target_id=base_entities["registry_target_id"],
            )
            db.session.add(b2)
            db.session.commit()

        response = builder_client.get("/builders/")
        html = response.data.decode()
        assert html.count('class="build-group-btn') == 1
        assert html.count('class="build-one-btn') == 0

    def test_each_row_has_build_and_delete_buttons(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        response = builder_client.get("/builders/")
        html = response.data.decode()
        assert f'data-builder-id="{builder_id}"' in html
        assert f"delete-builder-modal-{builder_id}" in html


class TestRepoInfo:
    def test_returns_branches_and_dockerfiles(self, builder_client, app, base_entities):
        response = builder_client.get(f"/builders/api/repo-info/{base_entities['repository_id']}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["branches"] == ["main", "develop"]
        assert data["dockerfiles"] == ["Dockerfile"]
        assert data["default_branch"] == "main"


class TestEditFormBranchChoicesSurviveAMissingClone:
    """Regression coverage for the "already-saved branch silently vanishes
    from its own edit dropdown" bug: when a repo's local clone is missing
    (e.g. lost on container restart before the repo_clones volume existed),
    the live branch picker returns an empty list, but a Builder's own
    already-saved default_branch must still render as a selectable/selected
    option — see _branch_choices in app/blueprints/builders/routes.py.
    """

    def test_saved_branch_still_selectable_when_picker_returns_empty(
        self, builder_client, app, base_entities, _fake_git_provider
    ):
        _fake_git_provider.branches = []  # simulates a missing local clone

        with app.app_context():
            builder = _make_builder(base_entities, name="b1", default_branch="release/1.0")
            db.session.commit()
            builder_id = builder.id

        response = builder_client.get("/builders/")
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert '<option selected value="release/1.0">release/1.0</option>' in html

    def test_other_saved_branches_are_unaffected_when_present_in_the_live_list(
        self, builder_client, app, base_entities, _fake_git_provider
    ):
        _fake_git_provider.branches = ["main", "develop"]

        with app.app_context():
            builder = _make_builder(base_entities, name="b1", default_branch="main")
            db.session.commit()

        response = builder_client.get("/builders/")
        html = response.get_data(as_text=True)

        # Exactly one "main" option in this builder's branch select — the
        # defensive fallback must not duplicate a branch already present in
        # the live list.
        assert html.count('value="main">main</option>') == 1


class TestCreateBuilder:
    def test_creates_a_builder_with_build_args(self, builder_client, app, base_entities):
        response = builder_client.post(
            "/builders/create",
            data={
                "create-builder-name": "my-builder",
                "create-builder-version_id": str(base_entities["version_id"]),
                "create-builder-repository_id": str(base_entities["repository_id"]),
                "create-builder-default_branch": "main",
                "create-builder-dockerfile_path": "Dockerfile",
                "create-builder-registry_target_id": str(base_entities["registry_target_id"]),
                "build_arg_key": ["ENV"],
                "build_arg_value": ["production"],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            builder = Builder.query.filter_by(name="my-builder").first()
            assert builder is not None
            assert builder.default_build_args == {"ENV": "production"}

    def test_creates_a_builder_with_a_custom_image_name(self, builder_client, app, base_entities):
        response = builder_client.post(
            "/builders/create",
            data={
                "create-builder-name": "my-builder",
                "create-builder-version_id": str(base_entities["version_id"]),
                "create-builder-repository_id": str(base_entities["repository_id"]),
                "create-builder-default_branch": "main",
                "create-builder-image_name": "custom-image",
                "create-builder-dockerfile_path": "Dockerfile",
                "create-builder-registry_target_id": str(base_entities["registry_target_id"]),
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            builder = Builder.query.filter_by(name="my-builder").first()
            assert builder.image_name == "custom-image"

    def test_creates_a_builder_with_a_group(self, builder_client, app, base_entities):
        response = builder_client.post(
            "/builders/create",
            data={
                "create-builder-name": "my-builder",
                "create-builder-version_id": str(base_entities["version_id"]),
                "create-builder-repository_id": str(base_entities["repository_id"]),
                "create-builder-default_branch": "main",
                "create-builder-group_name": "backend",
                "create-builder-dockerfile_path": "Dockerfile",
                "create-builder-registry_target_id": str(base_entities["registry_target_id"]),
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            builder = Builder.query.filter_by(name="my-builder").first()
            assert builder.group_name == "backend"

    def test_blank_group_name_falls_back_to_ungrouped(self, builder_client, app, base_entities):
        builder_client.post(
            "/builders/create",
            data={
                "create-builder-name": "my-builder",
                "create-builder-version_id": str(base_entities["version_id"]),
                "create-builder-repository_id": str(base_entities["repository_id"]),
                "create-builder-default_branch": "main",
                "create-builder-group_name": "   ",
                "create-builder-dockerfile_path": "Dockerfile",
                "create-builder-registry_target_id": str(base_entities["registry_target_id"]),
            },
            follow_redirects=True,
        )

        with app.app_context():
            builder = Builder.query.filter_by(name="my-builder").first()
            assert builder.group_name is None

    def test_blank_image_name_falls_back_to_none(self, builder_client, app, base_entities):
        builder_client.post(
            "/builders/create",
            data={
                "create-builder-name": "my-builder",
                "create-builder-version_id": str(base_entities["version_id"]),
                "create-builder-repository_id": str(base_entities["repository_id"]),
                "create-builder-default_branch": "main",
                "create-builder-image_name": "   ",
                "create-builder-dockerfile_path": "Dockerfile",
                "create-builder-registry_target_id": str(base_entities["registry_target_id"]),
            },
            follow_redirects=True,
        )

        with app.app_context():
            builder = Builder.query.filter_by(name="my-builder").first()
            assert builder.image_name is None

    def test_missing_name_does_not_create(self, builder_client, app, base_entities):
        builder_client.post(
            "/builders/create",
            data={
                "create-builder-version_id": str(base_entities["version_id"]),
                "create-builder-repository_id": str(base_entities["repository_id"]),
                "create-builder-default_branch": "main",
                "create-builder-registry_target_id": str(base_entities["registry_target_id"]),
            },
        )
        with app.app_context():
            assert Builder.query.count() == 0


class TestEditBuilder:
    def test_edits_branch_and_dockerfile(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        builder_client.post(
            f"/builders/{builder_id}/edit",
            data={
                f"builder-{builder_id}-name": "b1",
                f"builder-{builder_id}-version_id": str(base_entities["version_id"]),
                f"builder-{builder_id}-repository_id": str(base_entities["repository_id"]),
                f"builder-{builder_id}-default_branch": "develop",
                f"builder-{builder_id}-dockerfile_path": "services/api/Dockerfile",
                f"builder-{builder_id}-registry_target_id": str(base_entities["registry_target_id"]),
            },
        )

        with app.app_context():
            builder = Builder.query.get(builder_id)
            assert builder.default_branch == "develop"
            assert builder.dockerfile_path == "services/api/Dockerfile"

    def test_edits_image_name(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        builder_client.post(
            f"/builders/{builder_id}/edit",
            data={
                f"builder-{builder_id}-name": "b1",
                f"builder-{builder_id}-version_id": str(base_entities["version_id"]),
                f"builder-{builder_id}-repository_id": str(base_entities["repository_id"]),
                f"builder-{builder_id}-default_branch": "main",
                f"builder-{builder_id}-image_name": "renamed-image",
                f"builder-{builder_id}-dockerfile_path": "Dockerfile",
                f"builder-{builder_id}-registry_target_id": str(base_entities["registry_target_id"]),
            },
        )

        with app.app_context():
            assert Builder.query.get(builder_id).image_name == "renamed-image"

    def test_edits_group_name(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        builder_client.post(
            f"/builders/{builder_id}/edit",
            data={
                f"builder-{builder_id}-name": "b1",
                f"builder-{builder_id}-version_id": str(base_entities["version_id"]),
                f"builder-{builder_id}-repository_id": str(base_entities["repository_id"]),
                f"builder-{builder_id}-default_branch": "main",
                f"builder-{builder_id}-group_name": "backend",
                f"builder-{builder_id}-dockerfile_path": "Dockerfile",
                f"builder-{builder_id}-registry_target_id": str(base_entities["registry_target_id"]),
            },
        )

        with app.app_context():
            assert Builder.query.get(builder_id).group_name == "backend"


class TestDeleteBuilder:
    def test_deletes_a_builder_with_no_build_history(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        response = builder_client.post(f"/builders/{builder_id}/delete", follow_redirects=True)
        assert response.status_code == 200

        with app.app_context():
            assert Builder.query.get(builder_id) is None

    def test_blocks_deletion_when_build_history_exists(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            batch = BuildBatch(
                version_id=base_entities["version_id"],
                full_version_string="DEV.1.0.0.010101000000",
                bump_type="patch",
            )
            db.session.add(batch)
            db.session.flush()
            db.session.add(ImageBuild(batch_id=batch.id, builder_id=builder.id, branch_used="main"))
            db.session.commit()
            builder_id = builder.id

        response = builder_client.post(f"/builders/{builder_id}/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"recorded build" in response.data

        with app.app_context():
            assert Builder.query.get(builder_id) is not None


class TestBuildTrigger:
    def test_multiple_objects_mix_existing_and_new(self, builder_client, app, base_entities):
        """object_ids (existing Object rows) and new_object_names (free-typed,
        get-or-created) can both be submitted at once and both end up linked
        to the batch — the trigger modal's multi-picker sends both.
        """
        with app.app_context():
            from app.models import Object

            existing = Object(name="checkout-flow")
            db.session.add(existing)
            builder = _make_builder(base_entities)
            change_type = _make_change_type()
            db.session.commit()
            builder_id, change_type_id, existing_id = builder.id, change_type.id, existing.id

        builder_client.post(
            "/builders/build",
            data={
                "builder_ids": [str(builder_id)],
                "bump_type": "patch",
                "object_ids": [str(existing_id)],
                "new_object_names": ["payments"],
                "change_type_id": str(change_type_id),
            },
        )

        with app.app_context():
            batch = BuildBatch.query.first()
            assert {o.name for o in batch.objects} == {"checkout-flow", "payments"}
            # No duplicate Object row was created for the already-existing name.
            assert Object.query.filter_by(name="checkout-flow").count() == 1

    def test_single_builder_creates_a_queued_batch_without_bumping_the_version_yet(
        self, builder_client, app, base_entities
    ):
        """The version bump (and full_version_string) is deferred to the
        worker actually claiming the batch's first image — not to this POST
        — so a batch that never gets built (or that fails outright) never
        leaves the Version bumped for nothing. See worker._claim_next_job.
        """
        with app.app_context():
            builder = _make_builder(base_entities)
            change_type = _make_change_type()
            db.session.commit()
            builder_id = builder.id
            version_id = base_entities["version_id"]
            change_type_id = change_type.id

        response = builder_client.post(
            "/builders/build",
            data={
                "builder_ids": [str(builder_id)],
                "bump_type": "patch",
                "new_object_names": "backend",
                "change_type_id": str(change_type_id),
                "additional_description": "routine update",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            version = Version.query.get(version_id)
            assert (version.major, version.minor, version.patch) == (0, 0, 0)

            batch = BuildBatch.query.filter_by(version_id=version_id).first()
            assert batch is not None
            assert batch.bump_type == "patch"
            assert batch.full_version_string is None
            assert batch.status == "queued"
            # Staged on the batch, not yet a VersionDocumentation — that's
            # only created once the batch fully succeeds.
            assert [o.name for o in batch.objects] == ["backend"]
            assert batch.additional_description == "routine update"

            image_build = ImageBuild.query.filter_by(batch_id=batch.id).first()
            assert image_build.builder_id == builder_id
            assert image_build.branch_used == "main"
            assert image_build.status == "queued"

            assert VersionDocumentation.query.filter_by(batch_id=batch.id).first() is None

    def test_change_type_is_staged_on_the_batch(self, builder_client, app, base_entities):
        with app.app_context():
            change_type = _make_change_type()
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id, change_type_id = builder.id, change_type.id

        builder_client.post(
            "/builders/build",
            data={
                "builder_ids": [str(builder_id)],
                "bump_type": "patch",
                "new_object_names": "backend",
                "change_type_id": str(change_type_id),
            },
        )

        with app.app_context():
            batch = BuildBatch.query.first()
            assert batch.change_type_id == change_type_id

    def test_invalid_change_type_is_rejected(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        response = builder_client.post(
            "/builders/build",
            data={
                "builder_ids": [str(builder_id)],
                "bump_type": "patch",
                "new_object_names": "backend",
                "change_type_id": "00000000-0000-0000-0000-000000000000",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Change type is required" in response.data
        with app.app_context():
            assert BuildBatch.query.count() == 0

    def test_missing_bump_type_is_rejected(self, builder_client, app, base_entities):
        with app.app_context():
            change_type = _make_change_type()
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id, change_type_id = builder.id, change_type.id

        response = builder_client.post(
            "/builders/build",
            data={"builder_ids": [str(builder_id)], "new_object_names": "backend", "change_type_id": str(change_type_id)},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Bump type is required" in response.data
        with app.app_context():
            assert BuildBatch.query.count() == 0

    def test_missing_object_is_rejected(self, builder_client, app, base_entities):
        with app.app_context():
            change_type = _make_change_type()
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id, change_type_id = builder.id, change_type.id

        response = builder_client.post(
            "/builders/build",
            data={"builder_ids": [str(builder_id)], "bump_type": "patch", "change_type_id": str(change_type_id)},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Object is required" in response.data
        with app.app_context():
            assert BuildBatch.query.count() == 0

    def test_missing_change_type_is_rejected(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        response = builder_client.post(
            "/builders/build",
            data={"builder_ids": [str(builder_id)], "bump_type": "patch", "new_object_names": "backend"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Change type is required" in response.data
        with app.app_context():
            assert BuildBatch.query.count() == 0

    def test_branch_override_in_the_request_is_ignored_the_builders_default_branch_is_used(
        self, builder_client, app, base_entities
    ):
        """The build-trigger modal no longer offers a branch override field —
        even a hand-crafted request that still includes the old
        branch_override_<id> key must not change which branch gets built.
        """
        with app.app_context():
            change_type = _make_change_type()
            builder = _make_builder(base_entities, default_branch="main")
            db.session.commit()
            builder_id, change_type_id = builder.id, change_type.id

        builder_client.post(
            "/builders/build",
            data={
                "builder_ids": [str(builder_id)],
                f"branch_override_{builder_id}": "feature-x",
                "bump_type": "patch",
                "new_object_names": "backend",
                "change_type_id": str(change_type_id),
            },
        )

        with app.app_context():
            image_build = ImageBuild.query.first()
            assert image_build.branch_used == "main"

    def test_multiple_builders_sharing_a_version_create_one_batch(
        self, builder_client, app, base_entities
    ):
        with app.app_context():
            builder1 = _make_builder(base_entities, name="b1")
            builder2 = _make_builder(base_entities, name="b2")
            change_type = _make_change_type()
            db.session.commit()
            builder1_id, builder2_id = builder1.id, builder2.id
            version_id = base_entities["version_id"]
            change_type_id = change_type.id

        builder_client.post(
            "/builders/build",
            data={
                "builder_ids": [str(builder1_id), str(builder2_id)],
                "bump_type": "minor",
                "new_object_names": "backend",
                "change_type_id": str(change_type_id),
            },
        )

        with app.app_context():
            batches = BuildBatch.query.filter_by(version_id=version_id).all()
            assert len(batches) == 1
            image_builds = ImageBuild.query.filter_by(batch_id=batches[0].id).all()
            assert len(image_builds) == 2
            assert {ib.builder_id for ib in image_builds} == {builder1_id, builder2_id}

    def test_builders_from_different_versions_are_rejected(self, builder_client, app, base_entities):
        with app.app_context():
            builder1 = _make_builder(base_entities, name="b1")
            other_version_type = VersionType(name="STAGING")
            db.session.add(other_version_type)
            db.session.flush()
            other_version = Version(name="other-svc", version_type_id=other_version_type.id)
            db.session.add(other_version)
            db.session.flush()
            builder2 = Builder(
                name="b2",
                version_id=other_version.id,
                repository_id=base_entities["repository_id"],
                default_branch="main",
                dockerfile_path="Dockerfile",
                registry_target_id=base_entities["registry_target_id"],
            )
            db.session.add(builder2)
            db.session.commit()
            builder1_id, builder2_id = builder1.id, builder2.id

        response = builder_client.post(
            "/builders/build",
            data={"builder_ids": [str(builder1_id), str(builder2_id)], "bump_type": "patch"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"must share the same Version" in response.data
        with app.app_context():
            assert BuildBatch.query.count() == 0

    def test_no_builders_selected_is_rejected(self, builder_client, app):
        response = builder_client.post(
            "/builders/build", data={"bump_type": "patch"}, follow_redirects=True
        )
        assert response.status_code == 200
        with app.app_context():
            assert BuildBatch.query.count() == 0

    def test_invalid_bump_type_is_rejected(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        builder_client.post(
            "/builders/build", data={"builder_ids": [str(builder_id)], "bump_type": "sideways"}
        )
        with app.app_context():
            assert BuildBatch.query.count() == 0


class _FakePreviewGitProvider:
    def __init__(self, messages=None):
        self.messages = messages or []
        self.synced = None

    def sync_repo(self, local_path, branch, repo_name=None):
        self.synced = (local_path, branch)

    def get_commits(self, local_path, since_ref=None, until_ref=None):
        return [{"sha": f"sha-{i}", "message": m} for i, m in enumerate(self.messages)]


class TestBuildPreview:
    def test_requires_at_least_one_builder(self, builder_client):
        response = builder_client.post("/builders/build/preview", data={})
        assert response.status_code == 400
        assert response.get_json()["error"]

    def test_rejects_builders_spanning_different_versions(self, builder_client, app, base_entities):
        with app.app_context():
            builder1 = _make_builder(base_entities, name="b1")
            other_version_type = VersionType(name="STAGING")
            db.session.add(other_version_type)
            db.session.flush()
            other_version = Version(name="other", version_type_id=other_version_type.id)
            db.session.add(other_version)
            db.session.flush()
            builder2 = Builder(
                name="b2",
                version_id=other_version.id,
                repository_id=base_entities["repository_id"],
                default_branch="main",
                dockerfile_path="Dockerfile",
                registry_target_id=base_entities["registry_target_id"],
            )
            db.session.add(builder2)
            db.session.commit()
            builder1_id, builder2_id = builder1.id, builder2.id

        response = builder_client.post(
            "/builders/build/preview",
            data={"builder_ids": [str(builder1_id), str(builder2_id)]},
        )
        assert response.status_code == 400

    def test_returns_409_while_a_build_is_running(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            batch = BuildBatch(version_id=base_entities["version_id"], bump_type="patch", status="running")
            db.session.add(batch)
            db.session.flush()
            db.session.add(
                ImageBuild(batch_id=batch.id, builder_id=builder.id, branch_used="main", status="running")
            )
            db.session.commit()
            builder_id = builder.id

        response = builder_client.post("/builders/build/preview", data={"builder_ids": [str(builder_id)]})
        assert response.status_code == 409

    def test_returns_heuristic_bump_type_and_ai_metadata(self, builder_client, app, base_entities, monkeypatch):
        with app.app_context():
            builder = _make_builder(base_entities)
            existing = Object(name="checkout-flow")
            db.session.add(existing)
            db.session.commit()
            builder_id, existing_object_id = builder.id, existing.id

        fake_git = _FakePreviewGitProvider(messages=["feat: add new payment method", "fix: checkout bug"])
        monkeypatch.setattr(
            "app.services.build.prefill.provider_for_git_source", lambda source: fake_git
        )
        monkeypatch.setattr(
            "app.services.build.prefill.suggest_metadata",
            lambda messages, additional_description=None: {
                "matched_object_names": ["checkout-flow"],
                "new_object_names": ["payments"],
                "change_type_name": None,
                "description": "Added a new payment method and fixed a checkout bug.",
            },
        )

        response = builder_client.post("/builders/build/preview", data={"builder_ids": [str(builder_id)]})
        assert response.status_code == 200
        data = response.get_json()
        assert data["bump_type"] == "minor"  # feat: beats fix:
        assert data["matched_objects"] == [{"id": str(existing_object_id), "name": "checkout-flow"}]
        assert data["new_object_names"] == ["payments"]
        assert data["description"] == "Added a new payment method and fixed a checkout bug."
        assert data["commit_count"] == 2

    def test_syncs_the_repo_before_reading_commits(self, builder_client, app, base_entities, monkeypatch):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        fake_git = _FakePreviewGitProvider(messages=[])
        monkeypatch.setattr(
            "app.services.build.prefill.provider_for_git_source", lambda source: fake_git
        )
        monkeypatch.setattr(
            "app.services.build.prefill.suggest_metadata",
            lambda messages, additional_description=None: {
                "matched_object_names": [],
                "new_object_names": [],
                "change_type_name": None,
                "description": "",
            },
        )

        builder_client.post("/builders/build/preview", data={"builder_ids": [str(builder_id)]})
        assert fake_git.synced == ("/tmp/repo", "main")

    def test_a_git_failure_for_one_builder_does_not_block_the_response(
        self, builder_client, app, base_entities, monkeypatch
    ):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        class _RaisingGitProvider:
            def sync_repo(self, local_path, branch, repo_name=None):
                raise RuntimeError("network unreachable")

        monkeypatch.setattr(
            "app.services.build.prefill.provider_for_git_source", lambda source: _RaisingGitProvider()
        )
        monkeypatch.setattr(
            "app.services.build.prefill.suggest_metadata",
            lambda messages, additional_description=None: {
                "matched_object_names": [],
                "new_object_names": [],
                "change_type_name": None,
                "description": "",
            },
        )

        response = builder_client.post("/builders/build/preview", data={"builder_ids": [str(builder_id)]})
        assert response.status_code == 200
        assert response.get_json()["commit_count"] == 0


class TestStatusEndpoint:
    def test_status_when_idle(self, builder_client):
        response = builder_client.get("/builders/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["busy"] is False
        assert data["running"] is None

    def test_status_with_a_running_build(self, builder_client, app, base_entities):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.flush()
            batch = BuildBatch(
                version_id=base_entities["version_id"],
                full_version_string="DEV.0.0.1.010101010101",
                bump_type="patch",
                status="running",
            )
            db.session.add(batch)
            db.session.flush()
            image_build = ImageBuild(
                batch_id=batch.id, builder_id=builder.id, branch_used="main", status="running"
            )
            db.session.add(image_build)
            db.session.commit()

        response = builder_client.get("/builders/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["busy"] is True
        assert data["running"]["builder"] == "b1"
        assert data["running"]["batch_version"] == "DEV.0.0.1.010101010101"


class TestPerBuilderRoleAccess:
    def test_builder_with_no_allowed_roles_is_hidden_from_a_limited_role(
        self, limited_client, app, base_entities
    ):
        with app.app_context():
            _make_builder(base_entities, name="restricted-image-builder")
            db.session.commit()

        response = limited_client.get("/builders/")
        assert response.status_code == 200
        assert b"restricted-image-builder" not in response.data

    def test_builder_with_the_matching_allowed_role_is_visible(
        self, limited_client, app, base_entities, limited_role
    ):
        with app.app_context():
            builder = _make_builder(base_entities, name="restricted-image-builder")
            builder.allowed_roles = [Role.query.get(limited_role)]
            db.session.commit()

        response = limited_client.get("/builders/")
        assert response.status_code == 200
        assert b"restricted-image-builder" in response.data

    def test_builder_manage_permission_always_sees_every_builder(self, builder_client, app, base_entities):
        with app.app_context():
            _make_builder(base_entities, name="restricted-image-builder")
            db.session.commit()

        response = builder_client.get("/builders/")
        assert b"restricted-image-builder" in response.data

    def test_build_is_rejected_for_a_builder_not_in_the_limited_roles_allowlist(
        self, limited_client, app, base_entities
    ):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        response = limited_client.post(
            "/builders/build", data={"builder_ids": [str(builder_id)], "bump_type": "patch"}
        )
        assert response.status_code == 403
        with app.app_context():
            assert BuildBatch.query.count() == 0

    def test_build_is_allowed_for_a_builder_in_the_limited_roles_allowlist(
        self, limited_client, app, base_entities, limited_role
    ):
        with app.app_context():
            builder = _make_builder(base_entities)
            builder.allowed_roles = [Role.query.get(limited_role)]
            change_type = _make_change_type()
            db.session.commit()
            builder_id, change_type_id = builder.id, change_type.id

        response = limited_client.post(
            "/builders/build",
            data={
                "builder_ids": [str(builder_id)],
                "bump_type": "patch",
                "new_object_names": "backend",
                "change_type_id": str(change_type_id),
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            assert BuildBatch.query.count() == 1

    def test_create_builder_persists_allowed_roles(self, builder_client, app, base_entities, limited_role):
        response = builder_client.post(
            "/builders/create",
            data={
                "create-builder-name": "my-builder",
                "create-builder-version_id": str(base_entities["version_id"]),
                "create-builder-repository_id": str(base_entities["repository_id"]),
                "create-builder-default_branch": "main",
                "create-builder-dockerfile_path": "Dockerfile",
                "create-builder-registry_target_id": str(base_entities["registry_target_id"]),
                "create-builder-allowed_role_ids": [str(limited_role)],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            builder = Builder.query.filter_by(name="my-builder").first()
            assert [role.id for role in builder.allowed_roles] == [limited_role]

    def test_edit_builder_updates_allowed_roles(self, builder_client, app, base_entities, limited_role):
        with app.app_context():
            builder = _make_builder(base_entities)
            db.session.commit()
            builder_id = builder.id

        response = builder_client.post(
            f"/builders/{builder_id}/edit",
            data={
                f"builder-{builder_id}-name": "b1",
                f"builder-{builder_id}-version_id": str(base_entities["version_id"]),
                f"builder-{builder_id}-repository_id": str(base_entities["repository_id"]),
                f"builder-{builder_id}-default_branch": "main",
                f"builder-{builder_id}-dockerfile_path": "Dockerfile",
                f"builder-{builder_id}-registry_target_id": str(base_entities["registry_target_id"]),
                f"builder-{builder_id}-allowed_role_ids": [str(limited_role)],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            builder = Builder.query.get(builder_id)
            assert [role.id for role in builder.allowed_roles] == [limited_role]
